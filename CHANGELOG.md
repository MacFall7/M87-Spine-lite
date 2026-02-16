# Changelog

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
