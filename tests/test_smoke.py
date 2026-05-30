"""
Spine Lite — Smoke tests

Validates that the governance guard and receipt system function correctly.
These run as part of the quality gate during governed sessions.
"""
import os
import sys
from pathlib import Path

# Ensure hooks are importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
os.environ.setdefault("SPINE_POLICY_PATH", str(ROOT / "governance" / "policy.yaml"))
os.environ.setdefault("SPINE_RECEIPT_SCHEMA", str(ROOT / "schemas" / "receipt.schema.json"))

import guard  # type: ignore


class TestClassification:
    """Guard must classify commands into correct effect classes."""

    def test_safe_commands(self):
        for cmd in ["git status", "ls -la", "cat README.md", "pytest"]:
            result = guard.classify_command(cmd)
            assert result.value == "SHELL_SAFE", f"{cmd} → {result.value}, expected SHELL_SAFE"

    def test_dangerous_commands(self):
        for cmd in ["rm -rf /", "sudo apt install", "chmod 777 .", "git push --force origin"]:
            result = guard.classify_command(cmd)
            assert result.value == "SHELL_DANGEROUS", f"{cmd} → {result.value}, expected SHELL_DANGEROUS"

    def test_network_commands(self):
        for cmd in ["curl https://evil.com", "wget http://x.com", "pip install requests", "npm install express"]:
            result = guard.classify_command(cmd)
            assert result.value == "NETWORK_ATTEMPT", f"{cmd} → {result.value}, expected NETWORK_ATTEMPT"

    def test_mutating_commands(self):
        for cmd in ["git add .", "mkdir src", "cp file.txt backup.txt"]:
            result = guard.classify_command(cmd)
            assert result.value == "SHELL_MUTATING", f"{cmd} → {result.value}, expected SHELL_MUTATING"

    def test_unknown_fails_closed(self):
        result = guard.classify_command("unknown_binary --evil-flag")
        assert result.value == "SHELL_DANGEROUS", f"Unknown → {result.value}, expected SHELL_DANGEROUS (fail-closed)"


class TestWriteScope:
    """Guard must enforce write scope boundaries."""

    def test_allowed_paths(self):
        for path in ["src/main.py", "tests/test_foo.py", "docs/guide.md"]:
            gr = guard.check_write_scope(path, str(ROOT))
            d = guard.guard_result_to_dict(gr)
            assert d["verdict"] == "ALLOW", f"Write to {path} should be ALLOW, got {d['verdict']}"

    def test_denied_paths(self):
        for path in [".env", ".env.local", "secrets.key", "credentials.json"]:
            gr = guard.check_write_scope(path, str(ROOT))
            d = guard.guard_result_to_dict(gr)
            assert d["verdict"] == "DENY", f"Write to {path} should be DENY, got {d['verdict']}"


class TestPolicyLoads:
    """Policy YAML must load and contain required structure."""

    def test_policy_loads(self):
        policy = guard.load_policy()
        assert policy is not None
        assert policy.get("enforcement_mode") == "strict"

    def test_invariants_present(self):
        policy = guard.load_policy()
        invariants = policy.get("invariants", {})
        assert len(invariants) >= 7, f"Expected 7+ invariants, got {len(invariants)}"

    def test_autonomy_budget_present(self):
        policy = guard.load_policy()
        budget = policy.get("autonomy_budget", {})
        assert budget.get("max_steps", 0) > 0
        assert budget.get("breach_behavior") == "hard_halt"
