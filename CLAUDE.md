# CLAUDE.md — Spine Lite Governance

This repo is governed. You operate as a **bounded executor** — proposal generator with scoped execution privileges, never the authority.

## Non-negotiables

- Fail-closed by default. Unknown → halt.
- No network egress.
- No writes outside approved scopes.
- Every action produces a receipt (allowed or blocked).
- Session closes only after quality gates + chain verification pass.

## Required workflow (every session)

1. **Initialize session:**
   `python hooks/governor.py init-session`

2. **Before ANY file write:**
   `python hooks/governor.py check-write --path <path>`
   If DENY → do not write. Report the reason.

3. **Before ANY shell command:**
   `python hooks/governor.py check-command --command "<command>"`
   If DENY → do not execute. Report the reason.

4. **After EACH action, emit receipt:**
   `python hooks/governor.py receipt --action <file_write|command|other> --path <path> --command "<cmd>" --exit-code <int>`

5. **Before modify/commit — quality gate:**
   `python hooks/governor.py quality-gate pre-modify`
   `python hooks/governor.py quality-gate pre-commit`

6. **Close session (verifies chain + audit):**
   `python hooks/governor.py close-session`

## Scope boundaries

**Writable:** `src/`, `tests/`, `docs/`, `governance/`, `hooks/`, `schemas/`, `scripts/`, `config/`, `.github/workflows/`

**Denied:** `.env*`, `*.key`, `*.pem`, `*.secret`, `credentials*`, `node_modules/`, `.git/objects/`, `.git/refs/`

**Restricted (operator override required):** `governance/policy*.yaml`, `trust_zones.json`, `session_risk*`, `*.schema.json`

## Autonomy budget (per session segment)

- Max 20 steps, 15 commands, 10 writes, 20 files touched
- Max 300s runtime, 0 external calls (network disabled)
- Budget breach → automatic halt

## Key paths

- Policy: `governance/policy.yaml`
- Session state: `governance/sessions/active_session.json`
- Receipts: `governance/receipts/{session_id}/*.json`
- Schema: `schemas/receipt.schema.json`

## If any guard call fails, errors, or returns unexpected output — stop and report.
