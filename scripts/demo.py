#!/usr/bin/env python3
"""
demo.py — Spine Lite governance demo

Exercises the full governance pipeline via CLI (subprocess calls to governor).
Verifies receipt risk_delta values are non-zero for blocked actions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "hooks" / "governor.py"

C_GREEN = "\033[0;32m"
C_RED = "\033[0;31m"
C_YELLOW = "\033[1;33m"
C_CYAN = "\033[0;36m"
C_GRAY = "\033[0;90m"
C_NC = "\033[0m"


def run(*args: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if expect_ok and cp.returncode != 0:
        print(f"{C_RED}Step failed: {' '.join(args)} rc={cp.returncode}{C_NC}")
        print(cp.stdout)
        print(cp.stderr, file=sys.stderr)
        raise SystemExit(1)
    return cp


def step(num: int, total: int, label: str) -> None:
    print(f"\n{C_YELLOW}[{num}/{total}] {label}{C_NC}")


def ok(msg: str) -> None:
    print(f"  {C_GREEN}✓ {msg}{C_NC}")


def fail(msg: str) -> None:
    print(f"  {C_RED}✗ {msg}{C_NC}")


def info(msg: str) -> None:
    print(f"  {C_GRAY}{msg}{C_NC}")


TOTAL = 10


def main() -> int:
    print(f"\n{C_CYAN}{'='*60}")
    print("  Spine Lite — Governance Demo")
    print(f"{'='*60}{C_NC}")

    # 1. Init session
    step(1, TOTAL, "Initialize governed session")
    cp = run("init-session")
    session_id = cp.stdout.strip().splitlines()[-1]
    ok(f"Session: {session_id}")

    # 2. Allowed command: git status
    step(2, TOTAL, "Allowed command: git status")
    cp = run("check-command", "--command", "git status")
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")
    run("receipt", "--action", "command", "--command", "git status",
        "--exit-code", "0", "--proposal-id", "demo-1",
        "--description", "Execute: git status")
    ok("Receipt emitted")

    # 3. Blocked: curl (NETWORK_ATTEMPT)
    step(3, TOTAL, "Blocked command: curl https://exfil.example.com")
    cp = run("check-command", "--command", "curl https://exfil.example.com", expect_ok=False)
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")
    run("receipt", "--action", "command", "--command", "curl https://exfil.example.com",
        "--exit-code", "1", "--proposal-id", "demo-2",
        "--description", "BLOCKED: curl network egress", expect_ok=False)
    ok("Receipt emitted (blocked)")

    # 4. Blocked: rm -rf (SHELL_DANGEROUS)
    step(4, TOTAL, "Blocked command: rm -rf /")
    cp = run("check-command", "--command", "rm -rf /", expect_ok=False)
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")
    run("receipt", "--action", "command", "--command", "rm -rf /",
        "--exit-code", "1", "--proposal-id", "demo-3",
        "--description", "BLOCKED: destructive command", expect_ok=False)
    ok("Receipt emitted (blocked)")

    # 5. Blocked: pip install (NETWORK_ATTEMPT)
    step(5, TOTAL, "Blocked command: pip install requests")
    cp = run("check-command", "--command", "pip install requests", expect_ok=False)
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")
    run("receipt", "--action", "command", "--command", "pip install requests",
        "--exit-code", "1", "--proposal-id", "demo-4",
        "--description", "BLOCKED: pip install (network)", expect_ok=False)
    ok("Receipt emitted (blocked)")

    # 6. Allowed write: docs/demo_note.md
    step(6, TOTAL, "Allowed write: docs/demo_note.md")
    cp = run("check-write", "--path", "docs/demo_note.md")
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")
    run("receipt", "--action", "file_write", "--path", "docs/demo_note.md",
        "--exit-code", "0", "--proposal-id", "demo-5",
        "--description", "Create docs/demo_note.md")
    ok("Receipt emitted")

    # 7. Blocked write: .env.production
    step(7, TOTAL, "Blocked write: .env.production")
    cp = run("check-write", "--path", ".env.production", expect_ok=False)
    d = json.loads(cp.stdout)
    ok(f"{d['verdict']} ({d['effect_class']})")

    # 8. Status check
    step(8, TOTAL, "Session status")
    cp = run("status")
    st = json.loads(cp.stdout)
    ok(f"Posture: {st.get('posture')}")
    ok(f"Risk total: {st.get('risk_total')}")
    info(f"Budget: {json.dumps(st.get('budget', {}))}")

    # 9. Verify receipt risk_delta values
    step(9, TOTAL, "Receipt risk_delta verification")
    receipt_dir = ROOT / "governance" / "receipts" / session_id
    zero_risk_blocked = 0
    total_blocked = 0
    for f in sorted(receipt_dir.glob("*.json")):
        r = json.loads(f.read_text())
        status = r["result"]["status"]
        rd = r["action"]["risk_delta"]
        ec = r["action"]["effect_class"]
        info(f"seq={r['sequence_number']} | {ec:20s} | {status:7s} | risk_delta={rd}")
        if status == "blocked":
            total_blocked += 1
            if rd == 0.0:
                zero_risk_blocked += 1

    if zero_risk_blocked > 0:
        fail(f"{zero_risk_blocked}/{total_blocked} blocked receipts have risk_delta=0.0 — FORENSIC GAP")
        return 1
    else:
        ok(f"All {total_blocked} blocked receipts have non-zero risk_delta")

    # 10. Quality gate + close
    step(10, TOTAL, "Quality gate + session close")
    (ROOT / "tests").mkdir(exist_ok=True)
    run("quality-gate", "pre-modify")
    ok("Quality gate passed")
    run("close-session")
    ok("Session closed — chain verified")

    # Summary
    print(f"\n{C_CYAN}{'='*60}")
    print("  Demo Complete — All Governance Gates Enforced")
    print(f"{'='*60}{C_NC}")
    print(f"""
  Allowed: 2 (git status + docs write)
  Blocked: 4 (curl + rm -rf + pip install + .env write)
  Receipts: {st.get('sequence', '?')} with valid hash chain
  Risk: {st.get('risk_total', '?')} → Posture: {st.get('posture', '?')}

  {C_GRAY}Receipts: governance/receipts/{session_id}/{C_NC}
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
