# Spine Lite -- Onboarding Guide

This guide walks you through installing, configuring, and verifying Spine Lite governance for your Claude Code projects. By the end, you'll have a governed Claude Code session with enforced policy, cryptographic receipts, and working quality gates.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Bootstrap](#bootstrap)
4. [Verify it works](#verify-it-works)
5. [Your first governed session](#your-first-governed-session)
6. [Understanding what gets blocked](#understanding-what-gets-blocked)
7. [Reading receipts](#reading-receipts)
8. [Choosing a policy template](#choosing-a-policy-template)
9. [Customizing the policy](#customizing-the-policy)
10. [Adding to an existing project](#adding-to-an-existing-project)
11. [CI integration](#ci-integration)
12. [Troubleshooting](#troubleshooting)
13. [Uninstalling](#uninstalling)

---

## Prerequisites

- **Python 3.10 or later** -- verify with `python3 --version`
- **Claude Code CLI** -- installed and authenticated (`claude --version`)
- **Git** -- for version control integration (receipt chain records git context)

No other dependencies required. The bootstrap script handles `pyyaml` installation automatically.

---

## Installation

### Option A: Clone into a new project

```bash
# Create your project directory
mkdir my-project && cd my-project
git init

# Clone Spine Lite
git clone https://github.com/MacFall7/M87-Spine-lite.git .spine

# Copy governance files into your project root
cp -r .spine/.claude .
cp -r .spine/CLAUDE.md .
cp -r .spine/governance .
cp -r .spine/hooks .
cp -r .spine/schemas .
cp -r .spine/scripts .
cp -r .spine/policy_templates .
cp -r .spine/docs .

# Optional: copy tests as a starting point
cp -r .spine/tests .
```

### Option B: Add to an existing project

```bash
cd /path/to/your/project

# Clone Spine Lite into a temporary directory
git clone https://github.com/MacFall7/M87-Spine-lite.git /tmp/spine-lite

# Copy governance files (will not overwrite your existing src/, tests/, etc.)
cp -r /tmp/spine-lite/.claude .
cp /tmp/spine-lite/CLAUDE.md .
cp -r /tmp/spine-lite/governance .
cp -r /tmp/spine-lite/hooks .
cp -r /tmp/spine-lite/schemas .
cp -r /tmp/spine-lite/scripts .
cp -r /tmp/spine-lite/policy_templates .
mkdir -p docs && cp /tmp/spine-lite/docs/*.md docs/

# Clean up
rm -rf /tmp/spine-lite
```

### Option C: One-line copy (if you already have the repo)

```bash
cp -r .spine/{.claude,CLAUDE.md,governance,hooks,schemas,scripts,policy_templates,docs} .
```

### What gets copied

| Directory/File | Purpose |
|---|---|
| `.claude/settings.json` | Registers governance hooks with Claude Code |
| `CLAUDE.md` | Fallback governance instructions (Claude reads this in-context) |
| `governance/policy.yaml` | Active governance policy (the rules) |
| `hooks/*.py` | Guard, receipt, governor, and entry point scripts |
| `schemas/receipt.schema.json` | JSON Schema for receipt validation |
| `scripts/bootstrap.sh` | Setup and validation script |
| `scripts/bootstrap_windows.ps1` | Windows equivalent |
| `scripts/demo.py` | End-to-end demo |
| `policy_templates/*.yaml` | Preset policy configurations |

---

## Bootstrap

The bootstrap script validates your environment, creates required directories, verifies all governance files are present, and runs smoke tests.

### Linux / macOS

```bash
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

### Windows

```powershell
.\scripts\bootstrap_windows.ps1
```

### What bootstrap does (7 steps)

1. **Python check** -- Verifies Python 3.10+ is available
2. **Dependencies** -- Installs `pyyaml` if missing; checks optional `jsonschema` and `redis`
3. **Directories** -- Creates `governance/`, `governance/receipts/`, `governance/sessions/`, `hooks/`, `schemas/`, `scripts/`, `config/`, `tests/`, `src/`, `docs/`
4. **File verification** -- Confirms all required governance files exist
5. **Environment** -- Writes `config/.env.spine` with environment variable defaults
6. **Policy validation** -- Loads `governance/policy.yaml`, verifies schema version, enforcement mode, and invariant count
7. **Smoke tests** -- Runs 5 classification tests:
   - Write to `src/main.py` -> ALLOW
   - Write to `.env.local` -> DENY (RESTRICTED_WRITE)
   - `git status` -> ALLOW (SHELL_SAFE)
   - `curl evil.com` -> DENY (NETWORK_ATTEMPT)
   - `rm -rf /` -> DENY (SHELL_DANGEROUS)

If any step fails, bootstrap exits with a non-zero code and prints the failure reason.

### Skip smoke tests

```bash
./scripts/bootstrap.sh --skip-smoke
```

Useful when you've modified the policy and want to validate structure only.

---

## Verify it works

After bootstrap succeeds, run the full demo:

```bash
python scripts/demo.py
```

This exercises the complete governance pipeline in 10 steps:

| Step | Action | Expected |
|------|--------|----------|
| 1 | Initialize session | Session ID printed |
| 2 | `git status` | ALLOW (SHELL_SAFE) |
| 3 | `curl https://exfil.example.com` | DENY (NETWORK_ATTEMPT) |
| 4 | `rm -rf /` | DENY (SHELL_DANGEROUS) |
| 5 | `pip install requests` | DENY (NETWORK_ATTEMPT) |
| 6 | Write `docs/demo_note.md` | ALLOW (SCOPED_WRITE) |
| 7 | Write `.env.production` | DENY (RESTRICTED_WRITE) |
| 8 | Session status check | Shows posture + risk total |
| 9 | Receipt verification | All blocked receipts have non-zero risk_delta |
| 10 | Quality gate + session close | Chain verified, session closed |

If the demo completes without errors, governance is fully operational.

---

## Your first governed session

### Step 1: Launch Claude Code

```bash
claude
```

When Claude Code starts, it reads `.claude/settings.json` and fires the `SessionStart` hook. This initializes:
- A new session ID (12-character hex)
- Session state at `governance/sessions/active_session.json`
- A receipt directory at `governance/receipts/{session_id}/`
- A zeroed budget (0 steps, 0 commands, 0 writes, 0 risk)

### Step 2: Work normally

Ask Claude Code to do things you'd normally ask:
- "Read src/main.py and explain it" -- **Allowed** (Read tool is not gated; Bash `cat` is SHELL_SAFE)
- "Run the tests" -- **Allowed** (`pytest` is SHELL_SAFE)
- "Create a new file at tests/test_feature.py" -- **Allowed** (SCOPED_WRITE, path in writable scope)
- "Fix the bug in src/utils.py" -- **Allowed** (Edit to `src/` is SCOPED_WRITE)

### Step 3: Watch denials happen

If Claude Code tries something outside policy:
- "Install the requests library" -- **Denied** (`pip install` is NETWORK_ATTEMPT)
- "Check if the API is up" -- **Denied** (any `curl`/`wget` is NETWORK_ATTEMPT)
- "Delete everything and start over" -- **Denied** (`rm -rf` is SHELL_DANGEROUS)

When an action is denied, Claude Code receives the denial reason via stderr. It sees a message like: `[SPINE] BLOCKED: Network egress blocked by containment policy: pip install requests`

Claude Code then adapts -- it knows the action was blocked and will try alternative approaches or inform you.

### Step 4: Check session status

You can check governance status at any time by looking at the session state:

```bash
python hooks/governor.py status
```

This shows: session_id, risk_total, posture, budget usage, quality gate state.

### Step 5: Session ends

When you close Claude Code (or the session ends), the `SessionEnd` hook fires:
1. Verifies the receipt chain integrity (all hashes check out)
2. Writes a session summary to the receipt directory
3. If quality gates haven't been run, session close may fail (configurable)

---

## Understanding what gets blocked

### Commands

Spine Lite classifies every shell command Claude Code attempts into one of four categories:

| Classification | Examples | Verdict (strict) |
|---|---|---|
| **SHELL_SAFE** | `git status`, `ls`, `cat`, `pytest`, `ruff check`, `grep` | ALLOW |
| **SHELL_MUTATING** | `git add`, `git commit`, `mkdir`, `cp`, `mv`, `touch` | ALLOW |
| **NETWORK_ATTEMPT** | `curl`, `wget`, `pip install`, `npm install`, `git push`, `ssh` | **DENY** |
| **SHELL_DANGEROUS** | `rm -rf`, `sudo`, `chmod 777`, `dd`, `mkfs`, unknown commands | **DENY** |

**The fail-closed principle**: Any command not recognized as safe or mutating is classified as SHELL_DANGEROUS and denied. This means new or unusual commands are blocked by default until explicitly allowed.

### File writes

| Target | Classification | Verdict (strict) |
|---|---|---|
| `src/**`, `tests/**`, `docs/**` | SCOPED_WRITE | ALLOW |
| `governance/**`, `hooks/**`, `schemas/**` | SCOPED_WRITE | ALLOW |
| `scripts/**`, `config/**`, `.github/workflows/**` | SCOPED_WRITE | ALLOW |
| `.env`, `.env.local`, `.env.production` | RESTRICTED_WRITE | **DENY** |
| `*.key`, `*.pem`, `*.secret` | RESTRICTED_WRITE | **DENY** |
| `credentials*` | RESTRICTED_WRITE | **DENY** |
| `node_modules/**` | RESTRICTED_WRITE | **DENY** |
| `.git/objects/**`, `.git/refs/**` | RESTRICTED_WRITE | **DENY** |
| `governance/policy*.yaml` | RESTRICTED (operator override) | **DENY** |
| `*.schema.json` | RESTRICTED (operator override) | **DENY** |
| Any other path not in writable list | SCOPED_WRITE (fail-closed) | **DENY** |

---

## Reading receipts

After a session, you can inspect the audit trail:

### Find receipt files

```bash
ls governance/receipts/
# Lists session directories (by session_id)

ls governance/receipts/{session_id}/
# Lists individual receipt JSON files, ordered by sequence number
```

### Receipt file anatomy

Each receipt file (e.g., `0001_abc123.json`) contains:

```json
{
  "receipt_id": "unique-uuid",
  "session_id": "abc123def456",
  "sequence_number": 1,
  "timestamp": "2026-02-16T12:00:00+00:00",
  "executor": {
    "type": "claude_code",
    "model": "claude-sonnet-4-20250514",
    "instance_id": "instance-uuid"
  },
  "action": {
    "tool": "shell",
    "operation": "command",
    "effect_class": "NETWORK_ATTEMPT",
    "risk_delta": 0.15,
    "description": "BLOCKED: curl network egress",
    "command": "curl https://exfil.example.com",
    "target_paths": [],
    "reversibility": "REVERSIBLE"
  },
  "result": {
    "status": "blocked",
    "exit_code": null,
    "blocked_by": "pre_exec_guard",
    "diff_hash": null,
    "files_created": [],
    "files_modified": [],
    "files_deleted": [],
    "stdout_truncated": null
  },
  "budget_snapshot": {
    "steps_used": 3,
    "steps_remaining": 17,
    "commands_used": 2,
    "writes_used": 0,
    "files_touched": 0,
    "runtime_elapsed_seconds": 12.5,
    "session_risk_score": 0.16,
    "current_posture": "NORMAL"
  },
  "git_context": {
    "branch": "main",
    "commit_before": "a1b2c3d",
    "commit_after": null
  },
  "previous_receipt_hash": "sha256-of-previous-receipt",
  "receipt_hash": "sha256-of-this-receipt"
}
```

### Verify the chain manually

```bash
python hooks/receipts.py verify governance/receipts/{session_id}
```

Returns a JSON report with `chain_valid: true/false` and any errors found.

### Session summary

After session close, `governance/receipts/{session_id}/session_summary.json` contains:
- Total actions, chain checksum, chain validity
- Effect class distribution (how many of each type)
- Total risk accumulated, final posture
- All files touched during the session

---

## Choosing a policy template

### Strict (default)

Best for: security-sensitive repositories, compliance requirements, production codebases.

- All network egress blocked
- Tight write scope (src, tests, docs, governance, hooks, schemas, scripts, config)
- Secrets and credentials always denied
- Full enforcement with hard-halt on budget breach

### Standard

Best for: most day-to-day development work where you need package management.

- Package managers allowed (pip install, npm install, yarn add)
- Broader write scope (includes node_modules, lock files)
- curl, wget, ssh still blocked
- Full enforcement otherwise

To switch:
```bash
cp policy_templates/standard.yaml governance/policy.yaml
./scripts/bootstrap.sh
```

### Minimal

Best for: getting visibility without restriction, evaluating impact before enabling strict mode.

- All commands allowed (audit-only, no blocking)
- All paths writable (including .env, credentials)
- Receipts still emitted for every action
- Chain still cryptographically verified
- No budget limits enforced

To switch:
```bash
cp policy_templates/minimal.yaml governance/policy.yaml
./scripts/bootstrap.sh
```

**Warning**: Minimal mode provides no secrets protection. Use only for evaluation.

---

## Customizing the policy

Edit `governance/policy.yaml` directly. Key sections:

### Write scope

```yaml
scope:
  writable_paths:
    - "src/**"
    - "tests/**"
    - "docs/**"
    # Add your project's directories here

  denied_write_paths:
    - ".env*"
    - "**/*.key"
    # Add sensitive paths here

  restricted_patterns:
    - "governance/policy*.yaml"
    # Paths requiring operator override
```

### Autonomy budget

```yaml
autonomy_budget:
  max_steps: 20              # Total operations per session
  max_commands: 15           # Shell invocations
  max_write_operations: 10   # File mutations
  max_files_touched: 20      # Unique file paths
  max_runtime_seconds: 300   # Wall-clock limit (5 min)
  max_external_calls: 0      # Network calls
  breach_behavior: hard_halt # hard_halt | warn_and_log | soft_limit
```

### Quality gates

```yaml
quality_gates:
  pre_modify:
    tests_must_pass: true
    lint_must_pass: true

  pre_commit:
    tests_must_pass: true
    lint_must_pass: true
    receipt_chain_valid: true

  test_runner:
    command: "pytest"           # Your test command
    timeout_seconds: 120

  linter:
    command: "ruff check ."    # Your linter
    timeout_seconds: 30
```

### Enforcement mode

```yaml
enforcement_mode: strict  # strict | advisory | disabled
```

- **strict** -- Guard blocks denied actions (default)
- **advisory** -- Guard classifies and logs but always returns ALLOW
- **disabled** -- No classification, receipts still emitted with minimal metadata

### After editing

Always re-validate:

```bash
# Structure only
./scripts/bootstrap.sh --skip-smoke

# Full validation including smoke tests
./scripts/bootstrap.sh
```

---

## Adding to an existing project

### Step 1: Check for conflicts

Spine Lite creates these at your project root:
- `.claude/settings.json` -- If you already have Claude Code hooks, merge them
- `CLAUDE.md` -- If you already have one, append the governance section
- `governance/` -- New directory (should not conflict)
- `hooks/` -- New directory (check for name collisions)
- `schemas/` -- New directory
- `scripts/` -- Check for existing bootstrap scripts

### Step 2: Merge `.claude/settings.json`

If you already have `.claude/settings.json` with hooks, add the Spine Lite hooks to your existing configuration:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/hooks/entry.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/hooks/entry.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash|Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/hooks/entry.py"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/hooks/entry.py"
          }
        ]
      }
    ]
  }
}
```

The `PreToolUse` matcher `Bash|Write|Edit` means the guard fires for shell commands and file operations. Read, Grep, Glob, and other read-only tools are not intercepted.

### Step 3: Update .gitignore

Add to your `.gitignore`:

```
governance/receipts/
governance/sessions/
config/.env.spine
```

### Step 4: Adjust writable paths

Edit `governance/policy.yaml` to include your project's directory structure:

```yaml
scope:
  writable_paths:
    - "src/**"
    - "tests/**"
    - "docs/**"
    - "lib/**"        # Add your directories
    - "app/**"
    - "components/**"
```

### Step 5: Bootstrap and verify

```bash
./scripts/bootstrap.sh
```

---

## CI integration

### GitHub Actions

Copy `.github/workflows/ci.yaml` from Spine Lite to your project. It runs:

1. Compile check on all Python hook files
2. Policy validation (YAML loads, strict mode, invariants present)
3. Classification matrix (11 commands tested)
4. Full end-to-end demo
5. Reference sweep

### Custom CI

Add these steps to any CI pipeline:

```bash
# Validate policy
python -c "
import yaml
from pathlib import Path
d = yaml.safe_load(Path('governance/policy.yaml').read_text())
assert d['enforcement_mode'] == 'strict'
assert len(d.get('invariants', {})) >= 7
print('Policy valid')
"

# Run classification tests
python -m pytest tests/test_smoke.py -v

# Run full demo
python scripts/demo.py
```

---

## Troubleshooting

### Bootstrap fails: "Python 3.10+ required"

Install Python 3.10 or later. On Ubuntu: `sudo apt install python3.10`. On macOS: `brew install python@3.12`.

Specify a custom Python binary: `PYTHON=python3.12 ./scripts/bootstrap.sh`

### Bootstrap fails: "pyyaml installation failed"

Try: `python3 -m pip install pyyaml --user` or `python3 -m pip install pyyaml --break-system-packages`

### Commands I need are being blocked

Unknown commands default to SHELL_DANGEROUS (fail-closed). To allow a command:

1. Add the command prefix to `SAFE_COMMAND_PREFIXES` or `MUTATING_COMMAND_PREFIXES` in `hooks/guard.py`
2. Or add it to `safe_command_prefixes` in `governance/policy.yaml` under the containment section
3. Re-run bootstrap to validate

### File writes are blocked unexpectedly

The file path must be within `scope.writable_paths` in `governance/policy.yaml`. Add your project directories there. Remember: paths not explicitly allowed are denied (fail-closed).

### "No active session" errors

This means `governance/sessions/active_session.json` doesn't exist. Either:
- Run `python hooks/governor.py init-session` manually, or
- Ensure `.claude/settings.json` is present and Claude Code fires the SessionStart hook

### Session close fails: "quality_gate_failed"

Run the quality gate manually to see which check is failing:
```bash
python hooks/governor.py quality-gate pre-commit
```

Common causes:
- Tests failing (fix your tests)
- Linter failing (fix lint errors)
- Receipt chain empty (no actions were recorded -- did the hooks fire?)

### Session close fails: "chain verification failed"

The receipt chain has been tampered with or a receipt file is corrupted. Check `governance/receipts/{session_id}/` for malformed JSON files.

### Posture stuck in LOCKDOWN

Posture escalation is irreversible within a session. Start a new Claude Code session to reset. To make escalation less aggressive, increase the posture thresholds in `governance/policy.yaml`:

```yaml
session_risk:
  posture_levels:
    ELEVATED:
      threshold: 0.50    # was 0.30
    LOCKDOWN:
      threshold: 0.80    # was 0.50
```

### Hooks not firing

Verify `.claude/settings.json` exists at your project root and contains the correct hook configuration. Run `claude` from the project root directory (not a subdirectory).

### Redis errors in output

Redis is optional. If you see Redis connection warnings, either:
- Install Redis and set `SPINE_REDIS_ENABLED=true`, or
- Ignore the warnings (filesystem is always the primary store)

---

## Uninstalling

To remove Spine Lite from a project:

```bash
# Remove governance files
rm -rf .claude/settings.json governance/ hooks/ schemas/ scripts/bootstrap.sh \
       scripts/bootstrap_windows.ps1 scripts/demo.py policy_templates/ CLAUDE.md

# Remove from .gitignore (the governance-related entries)
# Remove CI workflow if you copied it
rm -f .github/workflows/ci.yaml
```

After removal, Claude Code will run without governance hooks -- no interception, no receipts, no scope enforcement.

---

## Next steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for a deep dive into the enforcement pipeline
- Read [POLICY_GUIDE.md](POLICY_GUIDE.md) for advanced policy customization
- Run `python scripts/demo.py` to see the full pipeline in action
- Start a governed Claude Code session and work normally -- governance is transparent until something is denied
