# Spine Lite — Architecture

## What it does

Spine Lite adds governance to Claude Code sessions. Every shell command and file write passes through a deterministic guard before execution. Every action — allowed or blocked — produces a cryptographically chained receipt. Sessions track cumulative risk and escalate posture automatically.

## How enforcement works

Claude Code's native hook system (`.claude/settings.json`) fires Python scripts at four lifecycle points:

```
SessionStart  →  entry.py  →  governor.py init-session
                                ↓
                           Creates session state, receipt directory, zeroed budget

PreToolUse    →  entry.py  →  governor.py check-command / check-write
                                ↓
                           guard.py classifies action → ALLOW or DENY
                                ↓
                           If DENY → entry.py exits with code 2 (stderr → Claude)
                           Claude Code runtime blocks the tool call

PostToolUse   →  entry.py  →  governor.py receipt
                                ↓
                           receipts.py emits hash-chained receipt JSON

SessionEnd    →  entry.py  →  governor.py close-session
                                ↓
                           Verifies receipt chain integrity, writes audit summary
```

The key property: **Claude Code cannot bypass the guard.** The runtime calls the hook before the tool executes. If the hook exits with code 2, the tool call is hard-blocked — it never runs. This is not JSON-advisory denial (which has known reliability bugs). Exit code 2 is the kernel boundary: the Claude Code runtime will not execute a tool past it, regardless of model behavior.

## Classification pipeline

Every command passes through a 5-step classification chain:

1. **Deny check** — Is this command in the explicit deny list? (rm -rf, sudo, chmod 777, etc.)
2. **Network check** — Does this command attempt network egress? (curl, wget, ssh, pip install, etc.)
3. **Safe check** — Is this a known read-only command? (git status, ls, cat, pytest, etc.)
4. **Mutating check** — Is this a known state-changing command? (git add, mkdir, cp, etc.)
5. **Fail-closed** — Unknown command → classified as SHELL_DANGEROUS → DENY

File writes pass through scope validation:
- **Allowed paths** — Configured in policy (src/, tests/, docs/, etc.)
- **Denied paths** — Secrets and credentials (.env*, *.key, *.pem, etc.)
- **Restricted paths** — Governance files (policy.yaml, schemas) — require operator override

## Risk model

Each denied action adds a risk delta to the session's cumulative score. Risk thresholds trigger posture escalation:

| Posture | Threshold | Effect |
|---------|-----------|--------|
| NORMAL | 0.00 | Standard operation |
| ELEVATED | 0.30 | Increased logging |
| LOCKDOWN | 0.50 | All writes auto-denied, dual classification required |
| HARD_TERMINATE | 1.00 | Session terminated |

Risk deltas by effect class:
- SHELL_SAFE: 0.00
- SHELL_MUTATING: 0.02
- SCOPED_WRITE: 0.02
- RESTRICTED_WRITE: 0.05
- NETWORK_ATTEMPT: 0.15
- SHELL_DANGEROUS: 0.10

## Receipt chain

Every receipt contains:
- Session ID, sequence number, timestamp
- Action record (tool, operation, effect class, risk delta, command/path)
- Result record (status, exit code, blocked_by)
- Budget snapshot (steps used, commands, writes, risk score, posture)
- SHA-256 hash of receipt content
- Hash of previous receipt (chain link)

The chain is verified on session close. Any tampering breaks the chain and prevents session closure.

## File layout

```
your-project/
├── .claude/settings.json      ← Claude Code reads this (registers hooks)
├── CLAUDE.md                  ← Fallback instructions if hooks fail
├── governance/
│   ├── policy.yaml            ← Governance rules (edit this)
│   ├── receipts/{session_id}/ ← Receipt chain (per session)
│   └── sessions/              ← Session state
├── hooks/
│   ├── entry.py               ← Hook dispatcher (called by Claude Code)
│   ├── governor.py            ← CLI wiring (session, budget, posture)
│   ├── guard.py               ← Classification + deny/allow logic
│   └── receipts.py            ← Receipt emission + chain verification
├── schemas/
│   └── receipt.schema.json    ← JSON Schema for receipt validation
├── scripts/
│   ├── bootstrap.sh           ← Setup + validation
│   ├── bootstrap_windows.ps1  ← Windows equivalent
│   └── demo.py                ← End-to-end demo
└── policy_templates/
    ├── strict.yaml            ← No network, tight scope (default)
    ├── standard.yaml          ← Package managers allowed
    └── minimal.yaml           ← Audit-only, no blocking
```

## Design principles

- **Incapacity over trust** — The guard *cannot* be bypassed, not "should not" be bypassed. Exit code 2 is a runtime hard gate.
- **Fail-closed** — Unknown inputs, missing data, or hook errors → exit 2 (deny).
- **Most-restrictive wins** — In ambiguous classifications, the stricter class applies.
- **Artifact-backed audit** — No action is invisible. Every tool call produces a receipt.
- **Model-independent** — Guards are pure Python. No prompt engineering, no model-specific logic.
