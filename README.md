# Spine Lite

**Governance guardrails for Claude Code.** Deterministic guards, cryptographic receipt chains, and runtime-enforced policy — so you can prove what your AI coding agent did, what it was denied, and verify the record wasn't tampered with.

## What it does

- **Blocks dangerous operations** before they execute (not after). Network egress, destructive commands, writes to secrets — denied via exit code 2, which is the hard enforcement boundary in Claude Code's runtime. Not prompt instructions. Not JSON advisory. The tool never runs.
- **Classifies every action** into typed effect classes (SHELL_SAFE, SHELL_DANGEROUS, NETWORK_ATTEMPT, etc.) with configurable risk weights.
- **Produces a cryptographic audit trail.** Every action — allowed or blocked — gets a hash-chained receipt. Session integrity is verified on close.
- **Escalates posture automatically.** Cumulative risk from denied actions triggers session escalation: NORMAL → ELEVATED → LOCKDOWN → HARD_TERMINATE.
- **Enforces via Claude Code's native hook system.** PreToolUse hooks exit with code 2 to block — the runtime's hard gate. The model cannot bypass exit code 2.

## Quick start

```bash
# Clone into your project
git clone https://github.com/m87studio/spine-lite.git .spine
cp -r .spine/{.claude,CLAUDE.md,governance,hooks,schemas,scripts,policy_templates,docs} .

# Bootstrap (validates Python, dependencies, policy, runs smoke tests)
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh

# Launch Claude Code — governance hooks fire automatically
claude
```

That's it. `.claude/settings.json` registers the hooks. Every Bash, Write, and Edit tool call now passes through the governance guard.

## See it in action

```bash
python scripts/demo.py
```

Runs a 10-step demo: initializes a governed session, executes allowed commands, attempts blocked commands (curl, rm -rf, pip install, .env write), verifies the receipt chain, and closes with an audit summary.

## How it works

Claude Code's native hook system calls `hooks/entry.py` at four lifecycle points:

| Event | What happens |
|-------|-------------|
| **SessionStart** | Initializes session state, receipt directory, zeroed budget |
| **PreToolUse** | Guard classifies the action → ALLOW or DENY. DENY exits with code 2, which hard-blocks the tool call at the runtime level. |
| **PostToolUse** | Emits a hash-chained receipt for the completed action |
| **SessionEnd** | Verifies receipt chain integrity, writes audit summary |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full enforcement pipeline.

## Policy templates

| Template | Description |
|----------|-------------|
| `strict.yaml` | No network, tight write scope, full enforcement. **Default.** |
| `standard.yaml` | Package managers allowed (pip, npm, yarn), broader write scope |
| `minimal.yaml` | Audit-only — no blocking, receipts still emitted |

```bash
# Switch to standard policy
cp policy_templates/standard.yaml governance/policy.yaml
./scripts/bootstrap.sh
```

See [docs/POLICY_GUIDE.md](docs/POLICY_GUIDE.md) for customization.

## What gets blocked (strict policy)

| Action | Effect class | Verdict |
|--------|-------------|---------|
| `git status` | SHELL_SAFE | ✅ ALLOW |
| `pytest` | SHELL_SAFE | ✅ ALLOW |
| `git add .` | SHELL_MUTATING | ✅ ALLOW |
| Write to `src/main.py` | SCOPED_WRITE | ✅ ALLOW |
| `curl https://...` | NETWORK_ATTEMPT | ❌ DENY |
| `pip install requests` | NETWORK_ATTEMPT | ❌ DENY |
| `rm -rf /` | SHELL_DANGEROUS | ❌ DENY |
| `sudo anything` | SHELL_DANGEROUS | ❌ DENY |
| Write to `.env` | RESTRICTED_WRITE | ❌ DENY |
| Write to `*.key` | RESTRICTED_WRITE | ❌ DENY |

## Requirements

- Python 3.10+
- `pyyaml` (installed automatically by bootstrap)
- Optional: `jsonschema` (for strict receipt validation), `redis` (for multi-session persistence)

## Manual usage (without Claude Code hooks)

The governance layer works as a standalone CLI:

```bash
# Initialize session
python hooks/governor.py init-session

# Check before writing
python hooks/governor.py check-write --path src/main.py

# Check before running a command
python hooks/governor.py check-command --command "git status"

# Emit receipt
python hooks/governor.py receipt --action command --command "git status" --exit-code 0

# Check session status
python hooks/governor.py status

# Run quality gate
python hooks/governor.py quality-gate pre-modify

# Close session (verifies chain)
python hooks/governor.py close-session
```

## Project structure

```
├── .claude/settings.json      ← Registers hooks with Claude Code
├── CLAUDE.md                  ← Fallback governance instructions
├── governance/
│   ├── policy.yaml            ← Active policy (edit this)
│   ├── receipts/              ← Hash-chained receipts per session
│   └── sessions/              ← Session state
├── hooks/
│   ├── entry.py               ← Hook dispatcher
│   ├── governor.py            ← Session lifecycle + CLI
│   ├── guard.py               ← Classification + deny/allow
│   └── receipts.py            ← Receipt emission + chain verification
├── schemas/
│   └── receipt.schema.json    ← Receipt JSON Schema
├── scripts/
│   ├── bootstrap.sh           ← Setup + smoke tests
│   ├── bootstrap_windows.ps1  ← Windows setup
│   └── demo.py                ← End-to-end demo
├── policy_templates/          ← Preset policies
└── docs/
    ├── ARCHITECTURE.md        ← How enforcement works
    └── POLICY_GUIDE.md        ← Policy customization
```

## .gitignore recommendations

```
governance/receipts/
governance/sessions/
config/.env.spine
```

## License

MIT — Copyright (c) 2026 M87 Studio LLC
