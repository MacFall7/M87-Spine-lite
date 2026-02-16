# Spine Lite — Policy Guide

## Choosing a template

Spine Lite ships with three policy templates in `policy_templates/`:

| Template | Network | Write scope | Blocking | Use case |
|----------|---------|-------------|----------|----------|
| **strict.yaml** | Blocked | Tight (src, tests, docs) | Full enforcement | Security-sensitive repos, compliance |
| **standard.yaml** | Package managers only | Broader (+ node_modules) | Full enforcement | Most development work |
| **minimal.yaml** | All allowed | All paths writable | Audit-only (no blocking) | Visibility without restriction |

To switch templates:
```bash
cp policy_templates/standard.yaml governance/policy.yaml
./scripts/bootstrap.sh  # re-validates
```

## Key sections to customize

### Write scope

```yaml
containment:
  workspace:
    allowed_write_paths:
      - "src/"
      - "tests/"
      - "docs/"
      # Add your paths here

    denied_write_paths:
      - ".env"
      - "*.key"
      - "*.pem"
      # Add sensitive paths here

    restricted_write_paths:
      - "governance/policy*.yaml"
      # Paths requiring operator override
```

### Command classification

```yaml
containment:
  commands:
    denied_commands:
      - "rm -rf"
      - "sudo"
      - "chmod 777"

    network_commands:
      - "curl"
      - "wget"
      - "ssh"
      - "pip install"     # Remove to allow pip
      - "npm install"     # Remove to allow npm
```

### Autonomy budget

```yaml
autonomy_budget:
  max_steps: 20              # Total operations per session segment
  max_commands: 15           # Shell invocations
  max_write_operations: 10   # File mutations
  max_external_calls: 0      # Network calls (0 = disabled)
  max_runtime_seconds: 300   # Wall-clock limit
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
    command: "pytest"         # Change to your test command
    timeout_seconds: 120

  linter:
    command: "ruff check ."   # Change to your linter
    timeout_seconds: 30
```

### Enforcement mode

```yaml
enforcement_mode: strict  # strict | advisory | disabled
```

- **strict** — Guard blocks denied actions. Default.
- **advisory** — Guard classifies and logs but always returns ALLOW. Useful for evaluating impact before enabling strict mode.
- **disabled** — No classification. Receipts still emitted with minimal metadata.

## Risk tuning

Risk deltas determine how quickly posture escalates:

```yaml
session_risk:
  risk_weights:
    SHELL_SAFE: 0.00
    SHELL_MUTATING: 0.02
    SCOPED_WRITE: 0.02
    RESTRICTED_WRITE: 0.05
    NETWORK_ATTEMPT: 0.15
    SHELL_DANGEROUS: 0.10

  posture_thresholds:
    ELEVATED: 0.30
    LOCKDOWN: 0.50
    HARD_TERMINATE: 1.00
```

Lower thresholds = faster escalation = more conservative. Higher risk weights for specific effect classes = those actions trigger escalation faster.

## Adding custom commands to safe/deny lists

The guard (`hooks/guard.py`) defines command prefix lists. To customize without editing Python, add entries to the policy YAML under `containment.commands` and the guard will read them at load time.

For commands that don't fit existing categories, the fail-closed default (SHELL_DANGEROUS) applies. To make a new command safe, add it to `safe_command_prefixes` in the policy.

## Validating changes

After editing `governance/policy.yaml`, always re-run bootstrap:

```bash
# Quick validation (policy loads, no smoke tests)
./scripts/bootstrap.sh --skip-smoke

# Full validation (includes smoke tests against new policy)
./scripts/bootstrap.sh
```

The smoke tests verify that your policy changes produce the expected ALLOW/DENY results for 5 canonical test cases.
