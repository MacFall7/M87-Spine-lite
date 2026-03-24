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

## Computer-use & MCP tool governance

Computer-use tools (`computer screenshot`, `computer click`, `computer type`, `computer scroll`, `computer key`, `str_replace_based_edit_tool`) are **not in any allowlist**. They fail-closed to `SHELL_DANGEROUS` and are always denied.

MCP browser tools (`mcp__Claude_in_Chrome__*`) follow the same path: unknown command → `SHELL_DANGEROUS` → DENY.

**Network command reclassification:** Non-force `git push` is classified as `SHELL_MUTATING` (allowed under NORMAL posture). Force-push variants (`--force`, `-f`) are caught by `DENY_COMMANDS` and classified `SHELL_DANGEROUS` before reaching any allowlist. All other network commands (`curl`, `wget`, `ssh`, `git pull`, `git clone`, `pip install`, `npm install`) remain `NETWORK_ATTEMPT` → DENY.

The catch-all at the end of `classify_command` returns `SHELL_DANGEROUS` for anything not matched. This is Invariant 3 (fail-closed) enforced structurally, not declaratively.

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
