"""
Spine Lite — Pre-Execution Guard
========================================
Hook: pre_write_guard, pre_exec_guard
Trigger: before_file_write, before_command_execution
Fail behavior: hard_stop

Enforces:
  - Invariant 1: Proposal ≠ Execution (gates all mutations)
  - Invariant 3: Fail-Closed Default (unknown → deny)
  - Scope boundaries from governance/policy.yaml
  - Autonomy budget limits
  - Effect classification

This module is model-agnostic (Invariant 6). No prompt tricks, no LLM calls.
Pure deterministic policy evaluation.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLICY_PATH = os.environ.get(
    "SPINE_POLICY_PATH",
    str(Path(__file__).parent.parent / "governance" / "policy.yaml"),
)

# Commands that are always denied regardless of context
DENY_COMMANDS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--force|-[a-zA-Z]*f[a-zA-Z]*r)\b"),  # rm -rf variants
    re.compile(r"\brm\s+-rf?\s+/"),                    # rm targeting root
    re.compile(r"\bdd\s+"),                              # dd (raw disk)
    re.compile(r"\bmkfs\b"),                             # filesystem creation
    re.compile(r"\bchmod\s+[0-7]*777\b"),               # world-writable
    re.compile(r"\bchown\b"),                            # ownership change
    re.compile(r"\bsudo\b"),                             # privilege escalation
    re.compile(r"\bsu\s"),                               # user switch
    re.compile(r"\bgit\s+push\s+.*--force\b"),          # force push
    re.compile(r"\bgit\s+push\s+-f\b"),                 # force push short
    re.compile(r">\s*/dev/"),                              # device writes
    re.compile(r"\b(?:powershell|pwsh)\b\s+-enc\b"),    # encoded powershell
]

NETWORK_COMMANDS: list[re.Pattern] = [
    re.compile(r"\bcurl\b"),
    re.compile(r"\bwget\b"),
    re.compile(r"\bnc\b"),
    re.compile(r"\bncat\b"),
    re.compile(r"\bssh\b"),
    re.compile(r"\bscp\b"),
    re.compile(r"\brsync\b"),
    re.compile(r"\bgit\s+pull\b"),
    re.compile(r"\bgit\s+clone\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\bnpm\s+install\b"),
]

# Network commands that are allowed (classified as SHELL_MUTATING, not NETWORK_ATTEMPT).
# Force-push variants are caught earlier by DENY_COMMANDS and never reach this check.
SAFE_NETWORK_COMMANDS: list[re.Pattern] = [
    re.compile(r"\bgit\s+push\b"),
]

# Commands classified as safe (read-only / non-mutating)
SAFE_COMMAND_PREFIXES: list[str] = [
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "which",
    "echo", "printf", "date", "pwd", "env", "whoami",
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote -v", "python -c", "python3 -c",
    "pytest", "ruff check", "ruff format --check", "mypy", "pyright",
    "npm test", "npm run lint", "npx tsc --noEmit",
    "cargo test", "cargo check", "cargo clippy",
]

# Commands classified as mutating but allowed
MUTATING_COMMAND_PREFIXES: list[str] = [
    "git add", "git commit", "git checkout", "git switch", "git stash",
    "mkdir", "touch", "mv", "cp",
        "ruff format",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class EffectClass(str, Enum):
    SAFE_READ = "SAFE_READ"
    SCOPED_WRITE = "SCOPED_WRITE"
    RESTRICTED_WRITE = "RESTRICTED_WRITE"
    SHELL_SAFE = "SHELL_SAFE"
    SHELL_MUTATING = "SHELL_MUTATING"
    SHELL_DANGEROUS = "SHELL_DANGEROUS"
    NETWORK_ATTEMPT = "NETWORK_ATTEMPT"


class GuardVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PostureLevel(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    LOCKDOWN = "LOCKDOWN"
    HARD_TERMINATE = "HARD_TERMINATE"


@dataclass
class GuardResult:
    verdict: GuardVerdict
    effect_class: EffectClass
    risk_delta: float
    reason: str
    target_paths: list[str] = field(default_factory=list)
    blocked_by: Optional[str] = None


@dataclass
class BudgetState:
    steps_used: int = 0
    commands_used: int = 0
    writes_used: int = 0
    files_touched: int = 0
    runtime_start: float = 0.0
    unique_files: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# Policy Loader
# ---------------------------------------------------------------------------

_policy_cache: dict | None = None
_policy_hash: str | None = None


def load_policy(path: str = POLICY_PATH) -> dict:
    """Load and cache governance policy. Fail-closed on parse error."""
    global _policy_cache, _policy_hash
    if _policy_cache is not None:
        return _policy_cache
    try:
        raw = Path(path).read_text(encoding="utf-8")
        _policy_hash = hashlib.sha256(raw.encode()).hexdigest()
        _policy_cache = yaml.safe_load(raw)
        if not isinstance(_policy_cache, dict):
            raise ValueError("Policy root must be a mapping")
        return _policy_cache
    except Exception as e:
        # Fail-closed: if policy can't load, nothing executes
        raise SystemExit(f"[SPINE GUARD] FATAL: Cannot load policy — {e}") from e


def get_policy_hash() -> str:
    """Return SHA-256 of loaded policy file."""
    if _policy_hash is None:
        load_policy()
    return _policy_hash  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Path Normalization (anti-traversal)
# ---------------------------------------------------------------------------

def normalize_path(raw_path: str, workspace_root: str = ".") -> str:
    """
    Normalize a path to prevent traversal attacks.
    Resolves symlinks, .., and redundant separators.
    Returns path relative to workspace root.
    """
    abs_workspace = Path(workspace_root).resolve()
    abs_target = (abs_workspace / raw_path).resolve()

    # Containment check: must remain within workspace
    try:
        abs_target.relative_to(abs_workspace)
    except ValueError:
        raise PermissionError(
            f"Path traversal blocked: {raw_path} resolves outside workspace"
        )

    return str(abs_target.relative_to(abs_workspace))


# ---------------------------------------------------------------------------
# Scope Matching
# ---------------------------------------------------------------------------

def _matches_any(path: str, patterns: list[str]) -> bool:
    """Check if path matches any glob pattern."""
    posix = PurePosixPath(path)
    for pattern in patterns:
        if fnmatch.fnmatch(str(posix), pattern):
            return True
        # Also check each parent segment for directory-level patterns
        for parent in posix.parents:
            if fnmatch.fnmatch(str(parent / posix.name), pattern):
                return True
    return False


def check_write_scope(
    target_path: str,
    workspace_root: str = ".",
    budget: BudgetState | None = None,
    posture: PostureLevel = PostureLevel.NORMAL,
) -> GuardResult:
    """
    Pre-write guard: validates file write against scope boundaries.

    Enforcement chain:
    1. Normalize path (anti-traversal)
    2. Check denied paths → DENY
    3. Check restricted patterns → RESTRICTED_WRITE (requires override)
    4. Check writable paths → SCOPED_WRITE
    5. Not in any allowed path → DENY (fail-closed)
    6. Check budget → DENY if exceeded
    7. Check posture → DENY if posture disallows writes
    """
    policy = load_policy()
    scope = policy.get("scope", {})

    writable = scope.get("writable_paths", [])
    denied = scope.get("denied_write_paths", [])
    restricted = scope.get("restricted_patterns", [])

    # Step 1: Normalize
    try:
        normalized = normalize_path(target_path, workspace_root)
    except PermissionError as e:
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=EffectClass.SHELL_DANGEROUS,
            risk_delta=0.10,
            reason=str(e),
            target_paths=[target_path],
            blocked_by="pre_write_guard",
        )

    # Step 2: Denied paths (absolute deny, checked first)
    if _matches_any(normalized, denied):
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=EffectClass.RESTRICTED_WRITE,
            risk_delta=0.08,
            reason=f"Path in denied_write_paths: {normalized}",
            target_paths=[normalized],
            blocked_by="pre_write_guard",
        )

    # Step 3: Restricted patterns (not auto-denied, but requires override)
    if _matches_any(normalized, restricted):
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=EffectClass.RESTRICTED_WRITE,
            risk_delta=0.08,
            reason=f"Path matches restricted_pattern — requires operator override: {normalized}",
            target_paths=[normalized],
            blocked_by="pre_write_guard",
        )

    # Step 4: Must be in writable paths
    if not _matches_any(normalized, writable):
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=EffectClass.SCOPED_WRITE,
            risk_delta=0.02,
            reason=f"Path not in writable_paths (fail-closed): {normalized}",
            target_paths=[normalized],
            blocked_by="pre_write_guard",
        )

    # Step 5: Posture check
    if posture in (PostureLevel.LOCKDOWN, PostureLevel.HARD_TERMINATE):
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=EffectClass.SCOPED_WRITE,
            risk_delta=0.02,
            reason=f"Writes denied under {posture.value} posture",
            target_paths=[normalized],
            blocked_by="risk_posture",
        )

    # Step 6: Budget check
    if budget is not None:
        budget_cfg = policy.get("autonomy_budget", {})
        max_writes = budget_cfg.get("max_write_operations", 10)
        max_files = budget_cfg.get("max_files_touched", 20)

        if budget.writes_used >= max_writes:
            return GuardResult(
                verdict=GuardVerdict.DENY,
                effect_class=EffectClass.SCOPED_WRITE,
                risk_delta=0.02,
                reason=f"Write budget exhausted ({budget.writes_used}/{max_writes})",
                target_paths=[normalized],
                blocked_by="budget_exceeded",
            )
        if len(budget.unique_files) >= max_files and normalized not in budget.unique_files:
            return GuardResult(
                verdict=GuardVerdict.DENY,
                effect_class=EffectClass.SCOPED_WRITE,
                risk_delta=0.02,
                reason=f"Files-touched budget exhausted ({len(budget.unique_files)}/{max_files})",
                target_paths=[normalized],
                blocked_by="budget_exceeded",
            )

    # ALLOW
    return GuardResult(
        verdict=GuardVerdict.ALLOW,
        effect_class=EffectClass.SCOPED_WRITE,
        risk_delta=0.02,
        reason=f"Write allowed: {normalized}",
        target_paths=[normalized],
    )


# ---------------------------------------------------------------------------
# Command Guard
# ---------------------------------------------------------------------------

def classify_command(command: str) -> EffectClass:
    """
    Classify a shell command into an effect class.

    Order (fail-closed):
      1) deny patterns (dangerous)
      2) network patterns (network attempt)
      3) safe prefixes
      4) mutating prefixes
      5) unknown (dangerous)
    """
    stripped = command.strip()

    # 1) Deny patterns first (must win against broader matches, e.g. git push --force)
    for pattern in DENY_COMMANDS:
        if pattern.search(stripped):
            return EffectClass.SHELL_DANGEROUS

    # 1.5) Safe network commands (e.g. non-force git push) → SHELL_MUTATING
    for pattern in SAFE_NETWORK_COMMANDS:
        if pattern.search(stripped):
            return EffectClass.SHELL_MUTATING

    # 2) Network attempts (blocked in disabled-network posture; tracked distinctly)
    for pattern in NETWORK_COMMANDS:
        if pattern.search(stripped):
            return EffectClass.NETWORK_ATTEMPT

    # 3) Safe commands
    for prefix in SAFE_COMMAND_PREFIXES:
        if stripped.startswith(prefix):
            return EffectClass.SHELL_SAFE

    # 4) Mutating commands
    for prefix in MUTATING_COMMAND_PREFIXES:
        if stripped.startswith(prefix):
            return EffectClass.SHELL_MUTATING

    # 5) Fail-closed: unknown command → SHELL_DANGEROUS
    return EffectClass.SHELL_DANGEROUS


def check_command(
    command: str,
    budget: BudgetState | None = None,
    posture: PostureLevel = PostureLevel.NORMAL,
) -> GuardResult:
    """
    Pre-exec guard: validates shell command against policy.

    Enforcement chain:
    1. Classify command effect
    2. SHELL_DANGEROUS → DENY
    3. NETWORK_ATTEMPT → DENY (containment)
    4. Check posture constraints
    5. Check budget
    6. ALLOW if all gates pass
    """
    policy = load_policy()
    effect = classify_command(command)

    risk_deltas = {
        EffectClass.SHELL_SAFE: 0.01,
        EffectClass.SHELL_MUTATING: 0.04,
        EffectClass.SHELL_DANGEROUS: 0.10,
        EffectClass.NETWORK_ATTEMPT: 0.15,
    }
    risk_delta = risk_deltas.get(effect, 0.10)

    # Step 1: Dangerous commands → DENY
    if effect == EffectClass.SHELL_DANGEROUS:
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=effect,
            risk_delta=risk_delta,
            reason=f"Command classified SHELL_DANGEROUS: {command[:100]}",
            blocked_by="pre_exec_guard",
        )

    # Step 2: Network attempts → DENY
    if effect == EffectClass.NETWORK_ATTEMPT:
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=effect,
            risk_delta=risk_delta,
            reason=f"Network egress blocked by containment policy: {command[:100]}",
            blocked_by="pre_exec_guard",
        )

    # Step 3: Posture constraints
    posture_limits = {
        PostureLevel.NORMAL: {EffectClass.SHELL_SAFE, EffectClass.SHELL_MUTATING},
        PostureLevel.ELEVATED: {EffectClass.SHELL_SAFE},
        PostureLevel.LOCKDOWN: {EffectClass.SHELL_SAFE},
        PostureLevel.HARD_TERMINATE: set(),
    }
    allowed_effects = posture_limits.get(posture, set())
    if effect not in allowed_effects:
        return GuardResult(
            verdict=GuardVerdict.DENY,
            effect_class=effect,
            risk_delta=risk_delta,
            reason=f"Effect {effect.value} not allowed under {posture.value} posture",
            blocked_by="risk_posture",
        )

    # Step 4: Budget check
    if budget is not None:
        budget_cfg = policy.get("autonomy_budget", {})
        max_commands = budget_cfg.get("max_commands", 15)
        max_runtime = budget_cfg.get("max_runtime_seconds", 300)

        if budget.commands_used >= max_commands:
            return GuardResult(
                verdict=GuardVerdict.DENY,
                effect_class=effect,
                risk_delta=risk_delta,
                reason=f"Command budget exhausted ({budget.commands_used}/{max_commands})",
                blocked_by="budget_exceeded",
            )

        elapsed = time.time() - budget.runtime_start if budget.runtime_start > 0 else 0
        if elapsed >= max_runtime:
            return GuardResult(
                verdict=GuardVerdict.DENY,
                effect_class=effect,
                risk_delta=risk_delta,
                reason=f"Runtime budget exhausted ({elapsed:.0f}s/{max_runtime}s)",
                blocked_by="budget_exceeded",
            )

    # ALLOW
    return GuardResult(
        verdict=GuardVerdict.ALLOW,
        effect_class=effect,
        risk_delta=risk_delta,
        reason=f"Command allowed [{effect.value}]: {command[:100]}",
    )


# ---------------------------------------------------------------------------
# Convenience: serialize GuardResult for receipt pipeline
# ---------------------------------------------------------------------------

def guard_result_to_dict(result: GuardResult) -> dict:
    """Serialize GuardResult for JSON embedding in receipts."""
    return {
        "verdict": result.verdict.value,
        "effect_class": result.effect_class.value,
        "risk_delta": result.risk_delta,
        "reason": result.reason,
        "target_paths": result.target_paths,
        "blocked_by": result.blocked_by,
    }


# ---------------------------------------------------------------------------
# CLI entry point (for testing / standalone validation)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python pre_exec_guard.py <write|command> <target>")
        print("  write:   python pre_exec_guard.py write src/main.py")
        print("  command: python pre_exec_guard.py command 'git status'")
        sys.exit(1)

    mode = sys.argv[1]
    target = sys.argv[2]

    if mode == "write":
        result = check_write_scope(target)
    elif mode == "command":
        result = check_command(target)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)

    print(json.dumps(guard_result_to_dict(result), indent=2))
    sys.exit(0 if result.verdict == GuardVerdict.ALLOW else 1)
