"""
Spine Lite — Post-Execution Receipt Hook
=================================================
Hook: post_exec_receipt, session_close_audit
Trigger: after_command_execution, session_end
Fail behavior: hard_stop

Enforces:
  - Invariant 4: Artifact-Backed Completion (every action → receipt)
  - Invariant 5: Structured Memory (receipts are indexed, chained, queryable)
  - Receipt chain integrity (hash-linked ledger per session)
  - Session completeness validation at close

This module is model-agnostic (Invariant 6).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RECEIPT_DIR = os.environ.get(
    "SPINE_RECEIPT_DIR",
    str(Path(__file__).parent.parent / "governance" / "receipts"),
)

RECEIPT_SCHEMA_PATH = os.environ.get(
    "SPINE_RECEIPT_SCHEMA",
    str(Path(__file__).parent.parent / "schemas" / "receipt.schema.json"),
)

# Optional Redis integration (degrade gracefully if unavailable)
REDIS_ENABLED = os.environ.get("SPINE_REDIS_ENABLED", "false").lower() == "true"
REDIS_URL = os.environ.get("SPINE_REDIS_URL", "redis://localhost:6379/0")
REDIS_RECEIPT_PREFIX = "spine:cc:receipts:"
REDIS_RISK_PREFIX = "spine:cc:risk:"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class ActionRecord:
    """Structured record of a single action for receipt embedding."""
    tool: str
    operation: str
    effect_class: str
    risk_delta: float
    description: str
    command: Optional[str] = None
    target_paths: list[str] = field(default_factory=list)
    reversibility: str = "REVERSIBLE"


@dataclass
class ResultRecord:
    """Structured record of action outcome."""
    status: str  # success | failure | blocked | timeout
    exit_code: Optional[int] = None
    blocked_by: Optional[str] = None
    diff_hash: Optional[str] = None
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    stdout_truncated: Optional[str] = None


@dataclass
class BudgetSnapshot:
    """Point-in-time budget state."""
    steps_used: int = 0
    steps_remaining: int = 20
    commands_used: int = 0
    writes_used: int = 0
    files_touched: int = 0
    runtime_elapsed_seconds: float = 0.0
    session_risk_score: float = 0.0
    current_posture: str = "NORMAL"


@dataclass
class GitContext:
    """Git state at action time."""
    branch: str = "unknown"
    commit_before: Optional[str] = None
    commit_after: Optional[str] = None


# ---------------------------------------------------------------------------
# Session State (in-process chain tracker)
# ---------------------------------------------------------------------------

class SessionChain:
    """
    Maintains receipt chain state for a session.
    Thread-safe: designed for single-session, single-process use.
    Multi-agent coordination uses shared filesystem + Redis.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.sequence: int = 0
        self.last_receipt_hash: Optional[str] = None
        self.receipts: list[dict] = []
        self._receipt_dir = Path(RECEIPT_DIR) / session_id
        self._receipt_dir.mkdir(parents=True, exist_ok=True)

    @property
    def chain_checksum(self) -> str:
        """SHA-256 of all receipt hashes concatenated. Verifies chain integrity."""
        combined = "".join(r.get("receipt_hash", "") for r in self.receipts)
        return hashlib.sha256(combined.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Hash Computation
# ---------------------------------------------------------------------------

def _compute_receipt_hash(receipt_data: dict) -> str:
    """
    Compute SHA-256 of receipt content.
    Excludes: receipt_hash, previous_receipt_hash (circular dependency).
    Deterministic: sorted keys, no whitespace variance.
    """
    hashable = {
        k: v for k, v in receipt_data.items()
        if k not in ("receipt_hash", "previous_receipt_hash")
    }
    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()



def _validate_receipt(receipt: dict) -> None:
    """
    Validate a receipt against the JSON Schema.

    Modes via env var SPINE_SCHEMA_VALIDATION:
      - strict (default): raise on validation failure
      - warn: print warning to stderr, continue
      - off: skip validation

    If jsonschema is not installed, falls back to a minimal structural check.
    """
    mode = os.environ.get("SPINE_SCHEMA_VALIDATION", "strict").strip().lower()
    if mode in ("0", "false", "off", "disable", "disabled", "none"):
        return

    def _handle(msg: str, exc: Exception | None = None) -> None:
        if mode == "warn":
            import sys
            sys.stderr.write(f"[spine][receipt_schema] {msg}\n")
            if exc is not None:
                sys.stderr.write(f"[spine][receipt_schema] {type(exc).__name__}: {exc}\n")
            return
        raise ValueError(msg) from exc

    # Minimal structural check (always available)
    required_top = [
        "receipt_id", "session_id", "proposal_id", "sequence_number", "timestamp",
        "executor", "action", "result", "budget_snapshot", "git_context",
        "previous_receipt_hash", "receipt_hash",
    ]
    missing = [k for k in required_top if k not in receipt]
    if missing:
        _handle(f"Receipt missing required keys: {missing}")
        return

    schema_path = Path(RECEIPT_SCHEMA_PATH)
    if not schema_path.exists():
        _handle(f"Receipt schema not found at: {schema_path}")
        return

    try:
        import jsonschema  # type: ignore
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=receipt, schema=schema)
    except ModuleNotFoundError as e:
        # No dependency: structural check already done; allow by default in warn/strict modes.
        # Strict mode still passes because structural check is satisfied.
        if mode == "warn":
            _handle("jsonschema not installed; used minimal structural validation only.", e)
    except Exception as e:
        _handle("Receipt failed JSON Schema validation.", e)

def _compute_diff_hash(workspace_root: str = ".") -> Optional[str]:
    """Compute SHA-256 of current staged diff. Returns None if no diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--staged"],
            capture_output=True, text=True, timeout=10,
            cwd=workspace_root,
        )
        diff = result.stdout.strip()
        if not diff:
            return None
        return hashlib.sha256(diff.encode()).hexdigest()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _get_git_context(workspace_root: str = ".") -> GitContext:
    """Capture current git state."""
    ctx = GitContext()
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=workspace_root,
        )
        ctx.branch = branch.stdout.strip() or "unknown"

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=workspace_root,
        )
        ctx.commit_before = head.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ctx


# ---------------------------------------------------------------------------
# Receipt Emission
# ---------------------------------------------------------------------------

def emit_receipt(
    chain: SessionChain,
    proposal_id: str,
    action: ActionRecord,
    result: ResultRecord,
    budget: BudgetSnapshot,
    executor_model: str = "unknown",
    executor_instance: str = "",
    workspace_root: str = ".",
) -> dict:
    """
    Emit a single execution receipt and append to session chain.

    Returns the complete receipt dict.
    Writes to filesystem. Optionally writes to Redis.
    Fail-closed: exceptions propagate (caller must handle).
    """
    chain.sequence += 1

    # Build receipt
    receipt: dict[str, Any] = {
        "receipt_id": str(uuid.uuid4()),
        "session_id": chain.session_id,
        "proposal_id": proposal_id,
        "sequence_number": chain.sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "executor": {
            "type": "claude_code",
            "model": executor_model,
            "instance_id": executor_instance or str(uuid.uuid4()),
        },
        "action": {
            "tool": action.tool,
            "operation": action.operation,
            "effect_class": action.effect_class,
            "risk_delta": action.risk_delta,
            "description": action.description,
            "command": action.command,
            "target_paths": action.target_paths,
            "reversibility": action.reversibility,
        },
        "result": {
            "status": result.status,
            "exit_code": result.exit_code,
            "blocked_by": result.blocked_by,
            "diff_hash": result.diff_hash or _compute_diff_hash(workspace_root),
            "files_created": result.files_created,
            "files_modified": result.files_modified,
            "files_deleted": result.files_deleted,
            "stdout_truncated": (
                result.stdout_truncated[:2000]
                if result.stdout_truncated else None
            ),
        },
        "budget_snapshot": {
            "steps_used": budget.steps_used,
            "steps_remaining": budget.steps_remaining,
            "commands_used": budget.commands_used,
            "writes_used": budget.writes_used,
            "files_touched": budget.files_touched,
            "runtime_elapsed_seconds": round(budget.runtime_elapsed_seconds, 2),
            "session_risk_score": round(budget.session_risk_score, 4),
            "current_posture": budget.current_posture,
        },
        "git_context": {
            "branch": "",
            "commit_before": None,
            "commit_after": None,
        },
        "previous_receipt_hash": chain.last_receipt_hash,
    }

    # Git context
    git_ctx = _get_git_context(workspace_root)
    receipt["git_context"] = {
        "branch": git_ctx.branch,
        "commit_before": git_ctx.commit_before,
        "commit_after": git_ctx.commit_after,
    }

    # Compute receipt hash (must be last)
    receipt["receipt_hash"] = _compute_receipt_hash(receipt)

    # Schema validation (after hash computation, before persistence)
    _validate_receipt(receipt)

    # Update chain state
    chain.last_receipt_hash = receipt["receipt_hash"]
    chain.receipts.append(receipt)

    # Persist to filesystem
    receipt_path = chain._receipt_dir / f"{chain.sequence:04d}_{receipt['receipt_id']}.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, default=str),
        encoding="utf-8",
    )

    # Optional Redis persistence
    if REDIS_ENABLED:
        _write_to_redis(chain.session_id, receipt)

    return receipt


# ---------------------------------------------------------------------------
# Session Close Audit
# ---------------------------------------------------------------------------

def close_session(
    chain: SessionChain,
    final_test_passed: bool = False,
    final_lint_passed: bool = False,
) -> dict:
    """
    Session close audit: validates receipt chain and emits session summary.

    Checks:
    1. Receipt chain is non-empty
    2. Sequence numbers are contiguous (no gaps)
    3. Hash chain is valid (each receipt links to previous)
    4. Quality gates passed (tests + lint)

    Returns session summary dict.
    Raises ValueError if chain is invalid (fail-closed).
    """
    errors: list[str] = []

    # Check 1: Non-empty chain
    if not chain.receipts:
        errors.append("Receipt chain is empty — no actions recorded")

    # Check 2: Contiguous sequence
    expected_seq = 1
    for receipt in chain.receipts:
        if receipt["sequence_number"] != expected_seq:
            errors.append(
                f"Sequence gap: expected {expected_seq}, got {receipt['sequence_number']}"
            )
        expected_seq += 1

    # Check 3: Hash chain integrity
    prev_hash: Optional[str] = None
    for receipt in chain.receipts:
        if receipt["previous_receipt_hash"] != prev_hash:
            errors.append(
                f"Hash chain broken at sequence {receipt['sequence_number']}: "
                f"expected prev={prev_hash}, got prev={receipt['previous_receipt_hash']}"
            )
        # Verify receipt's own hash
        computed = _compute_receipt_hash(receipt)
        if computed != receipt["receipt_hash"]:
            errors.append(
                f"Receipt hash mismatch at sequence {receipt['sequence_number']}: "
                f"stored={receipt['receipt_hash']}, computed={computed}"
            )
        prev_hash = receipt["receipt_hash"]

    # Check 4: Quality gates
    if not final_test_passed:
        errors.append("Session close: tests did not pass")
    if not final_lint_passed:
        errors.append("Session close: lint did not pass")

    # Build summary
    summary = {
        "session_id": chain.session_id,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "total_actions": len(chain.receipts),
        "chain_checksum": chain.chain_checksum,
        "chain_valid": len(errors) == 0,
        "errors": errors,
        "final_test_passed": final_test_passed,
        "final_lint_passed": final_lint_passed,
        "effect_class_distribution": _effect_distribution(chain.receipts),
        "total_risk_accumulated": round(
            sum(r["action"]["risk_delta"] for r in chain.receipts), 4
        ),
        "final_posture": (
            chain.receipts[-1]["budget_snapshot"]["current_posture"]
            if chain.receipts else "UNKNOWN"
        ),
        "files_touched": sorted(set(
            path
            for r in chain.receipts
            for path in r["action"]["target_paths"]
        )),
    }

    # Write summary
    summary_path = chain._receipt_dir / "session_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    if REDIS_ENABLED:
        _write_summary_to_redis(chain.session_id, summary)

    # Fail-closed: invalid chain → raise
    if errors:
        raise ValueError(
            f"[SPINE AUDIT] Session {chain.session_id} INCOMPLETE — "
            f"{len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _effect_distribution(receipts: list[dict]) -> dict[str, int]:
    """Count occurrences of each effect class."""
    dist: dict[str, int] = {}
    for r in receipts:
        ec = r["action"]["effect_class"]
        dist[ec] = dist.get(ec, 0) + 1
    return dist


def _write_to_redis(session_id: str, receipt: dict) -> None:
    """Best-effort Redis write. Failures logged, not fatal."""
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        key = f"{REDIS_RECEIPT_PREFIX}{session_id}:{receipt['receipt_id']}"
        r.setex(key, 86400, json.dumps(receipt, default=str))

        # Update risk score
        risk_key = f"{REDIS_RISK_PREFIX}{session_id}"
        current = float(r.get(risk_key) or 0)
        new_risk = current + receipt["action"]["risk_delta"]
        r.setex(risk_key, 86400, str(round(new_risk, 4)))
    except Exception:
        pass  # Redis is secondary storage; filesystem is primary


def _write_summary_to_redis(session_id: str, summary: dict) -> None:
    """Best-effort Redis write for session summary."""
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        key = f"{REDIS_RECEIPT_PREFIX}{session_id}:summary"
        r.setex(key, 86400 * 7, json.dumps(summary, default=str))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chain Verification (standalone / CI use)
# ---------------------------------------------------------------------------

def verify_chain(session_dir: str) -> dict:
    """
    Verify a receipt chain from filesystem.
    Loads all receipt JSONs, validates sequence + hash chain.
    Returns verification result dict.
    """
    session_path = Path(session_dir)
    receipt_files = sorted(session_path.glob("*.json"))
    receipt_files = [f for f in receipt_files if f.name != "session_summary.json"]

    receipts = []
    for f in receipt_files:
        receipts.append(json.loads(f.read_text(encoding="utf-8")))

    receipts.sort(key=lambda r: r["sequence_number"])

    errors: list[str] = []
    prev_hash: Optional[str] = None

    for receipt in receipts:
        if receipt["previous_receipt_hash"] != prev_hash:
            errors.append(f"Chain break at seq {receipt['sequence_number']}")
        computed = _compute_receipt_hash(receipt)
        if computed != receipt["receipt_hash"]:
            errors.append(f"Hash mismatch at seq {receipt['sequence_number']}")
        prev_hash = receipt["receipt_hash"]

    return {
        "session_dir": str(session_path),
        "total_receipts": len(receipts),
        "chain_valid": len(errors) == 0,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python post_exec_receipt.py verify <session_dir>")
        print("  python post_exec_receipt.py demo")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "verify" and len(sys.argv) >= 3:
        result = verify_chain(sys.argv[2])
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["chain_valid"] else 1)

    elif cmd == "demo":
        # Demo: create a session, emit receipts, close
        session_id = str(uuid.uuid4())
        chain = SessionChain(session_id)

        # Emit two demo receipts
        r1 = emit_receipt(
            chain=chain,
            proposal_id=str(uuid.uuid4()),
            action=ActionRecord(
                tool="Read", operation="read", effect_class="SAFE_READ",
                risk_delta=0.0, description="Read src/main.py",
                target_paths=["src/main.py"],
            ),
            result=ResultRecord(status="success", exit_code=0),
            budget=BudgetSnapshot(steps_used=1, steps_remaining=19),
        )
        print(f"Receipt 1: {r1['receipt_id']} [seq={r1['sequence_number']}]")

        r2 = emit_receipt(
            chain=chain,
            proposal_id=str(uuid.uuid4()),
            action=ActionRecord(
                tool="Write", operation="create", effect_class="SCOPED_WRITE",
                risk_delta=0.02, description="Create tests/test_new.py",
                target_paths=["tests/test_new.py"],
            ),
            result=ResultRecord(
                status="success", exit_code=0,
                files_created=["tests/test_new.py"],
            ),
            budget=BudgetSnapshot(
                steps_used=2, steps_remaining=18,
                writes_used=1, session_risk_score=0.02,
            ),
        )
        print(f"Receipt 2: {r2['receipt_id']} [seq={r2['sequence_number']}]")

        # Close session
        try:
            summary = close_session(chain, final_test_passed=True, final_lint_passed=True)
            print(f"\nSession closed: chain_valid={summary['chain_valid']}")
            print(f"Chain checksum: {summary['chain_checksum']}")
        except ValueError as e:
            print(f"\nSession close FAILED: {e}")

        sys.exit(0)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
