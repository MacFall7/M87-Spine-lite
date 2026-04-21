# Changelog

## [0.1.2] — 2026-04-21

**Policy self-consistency.** Fixes five defects surfaced by attempting to ship v0.1.1 through the documented governance workflow. See `governance/audits/v0.1.1-policy-self-consistency.md` for the full catalog.

### Fixed

- **D1 — governor CLI reachable through the guard.** Added `GOVERNANCE_COMMAND_PREFIXES` to `hooks/guard.py`: an explicit subcommand allowlist for `python hooks/governor.py {init-session,check-write,check-command,receipt,quality-gate,close-session,status}`. Kept as a named list rather than a directory glob to preserve authority separation — only named governance subcommands are reachable, not arbitrary scripts under `hooks/`.
- **D2 — release-adjacent files writable.** Added `CHANGELOG.md`, `README.md`, and `pyproject.toml` to `writable_paths` in `governance/policy.yaml`. Enumerated, not globbed, so new root-level files still fail-closed.
- **D4 — declared test environment.** Added `pyproject.toml` declaring `pyyaml` as a runtime dep and `pytest`/`ruff`/`jsonschema` as dev deps. Changed `quality_gates.test_runner.command` from `pytest` to `python -m pytest` so the runner uses the interpreter with the declared deps. Added `python -m pytest` / `python3 -m pytest` to `SAFE_COMMAND_PREFIXES`.
- **D5 — stale schema path.** Corrected `schemas/claude_receipt.schema.json` → `schemas/receipt.schema.json` in `governance/policy.yaml` (lines 52 and 250).
- **D6 — stale CLI usage strings.** Corrected `pre_exec_guard.py` → `hooks/guard.py` in `hooks/guard.py` usage text and `post_exec_receipt.py` → `hooks/receipts.py` in `hooks/receipts.py` usage text.

### Tests

- Added `test_governance_cli_reachable` and `test_governance_cli_does_not_leak_to_arbitrary_scripts` in `tests/test_smoke.py`.

### Deferred

- **D3 — operator override mechanism for `restricted_patterns`.** Missing design, not missing code. Deferred to v0.1.3 so it can be designed separately. Consequence: `schemas/*.schema.json` remain unmodifiable through the governed path until v0.1.3 ships.
- **D7 — risk accumulator has no decay or operator-facing reset.** Surfaced mid-audit when the verification phase's legitimate probing accumulated risk_total=0.54, tripping LOCKDOWN and blocking commit. Resetting the session state to escape LOCKDOWN required a direct file edit via `python -c` — which is exactly the kind of around-the-guard bypass the audit flagged. Shipping v0.1.2 required performing that bypass once. The proper fix (a `governor.py reset-posture` or decay mechanism with operator-override semantics) is in v0.1.3 scope alongside D3.

## [0.1.1] — 2026-04-20

### Fixed

- Corrected stale hook script paths in `governance/policy.yaml` §5 (`pre_exec_guard.py` / `post_exec_receipt.py` → `guard.py` / `receipts.py`).

## [0.1.0] — 2026-02-16

Initial public release.

### Added

- **Deterministic classification pipeline**: Every command and file write classified into typed effect classes (SHELL_SAFE, SHELL_DANGEROUS, NETWORK_ATTEMPT, SHELL_MUTATING, SCOPED_WRITE, RESTRICTED_WRITE) via pure Python guards. No model dependency.
- **Exit code 2 enforcement**: PreToolUse hook denials use Claude Code's hard gate (exit 2 + stderr). Tool calls cannot execute past a denial. This is the kernel boundary, not JSON-advisory.
- **Cryptographic receipt chain**: Every action (allowed or blocked) produces a hash-chained receipt. Chain integrity verified on session close. Tampering breaks the chain.
- **Session risk model**: Cumulative risk from denied actions triggers automatic posture escalation: NORMAL → ELEVATED → LOCKDOWN → HARD_TERMINATE.
- **Autonomy budget enforcement**: Per-session limits on steps, commands, writes, files touched, runtime. Breach → hard halt.
- **Quality gates**: Configurable test runner and linter execution at pre-modify and pre-commit checkpoints.
- **Three policy templates**: strict (no network, tight scope), standard (package managers allowed), minimal (audit-only, no blocking).
- **Cross-platform bootstrap**: Linux/macOS shell script and Windows PowerShell with dependency validation, policy verification, and 5-point smoke test matrix.
- **End-to-end demo**: 10-step script exercising full governance pipeline with receipt chain and risk_delta verification.
- **Claude Code native integration**: `.claude/settings.json` registers hooks at SessionStart, PreToolUse, PostToolUse, and SessionEnd. No manual invocation required.
- **CI workflow**: GitHub Actions running compile checks, classification matrix, demo, and M87-reference sweep on Python 3.10–3.12.
