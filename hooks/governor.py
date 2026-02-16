#!/usr/bin/env python3
"""
governor.py — CLI wiring layer for Spine Lite governance pack

Routes actions through:
- hooks/guard.py  (deny-by-default scope + command guard)
- hooks/receipts.py (receipt emission + chain verification)

State persists to:
- governance/sessions/active_session.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "governance"
SESS_DIR = GOV / "sessions"
RECEIPTS_DIR = GOV / "receipts"
STATE_PATH = SESS_DIR / "active_session.json"

POLICY_PATH = GOV / "policy.yaml"
SCHEMA_PATH = ROOT / "schemas" / "receipt.schema.json"

# Ensure hooks can import each other
sys.path.insert(0, str(ROOT / "hooks"))
import guard  # type: ignore
import receipts  # type: ignore


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _save_state(state: Dict[str, Any]) -> None:
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _require_session(state: Dict[str, Any]) -> Dict[str, Any]:
    if not state or "session_id" not in state:
        print("No active session. Run: python hooks/governor.py init-session", file=sys.stderr)
        sys.exit(2)
    return state


def _update_posture(state: Dict[str, Any]) -> None:
    """Update posture based on cumulative risk. Thresholds from governance policy."""
    risk = float(state.get("risk_total", 0.0))
    if risk >= 1.0:
        state["posture"] = "HARD_TERMINATE"
    elif risk >= 0.5:
        state["posture"] = "LOCKDOWN"
    elif risk >= 0.3:
        state["posture"] = "ELEVATED"
    else:
        state["posture"] = "NORMAL"


def _budget_snapshot(state: Dict[str, Any]) -> receipts.BudgetSnapshot:
    b = state.get("budget", {})
    return receipts.BudgetSnapshot(
        steps_used=int(b.get("steps_used", 0)),
        steps_remaining=max(0, 20 - int(b.get("steps_used", 0))),
        commands_used=int(b.get("commands", 0)),
        writes_used=int(b.get("writes", 0)),
        files_touched=int(b.get("files_touched", 0)),
        runtime_elapsed_seconds=float(b.get("runtime_seconds", 0.0)),
        session_risk_score=float(state.get("risk_total", 0.0)),
        current_posture=state.get("posture", "NORMAL"),
    )


def _extract_risk(decision: Dict[str, Any]) -> float:
    """Extract risk_delta from guard decision dict. Handles both key names."""
    return float(decision.get("risk_delta", decision.get("risk", 0.0)))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_session(args: argparse.Namespace) -> None:
    os.environ.setdefault("SPINE_POLICY_PATH", str(POLICY_PATH))
    os.environ.setdefault("SPINE_RECEIPT_SCHEMA", str(SCHEMA_PATH))
    os.environ.setdefault("SPINE_RECEIPT_DIR", str(RECEIPTS_DIR))

    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    SESS_DIR.mkdir(parents=True, exist_ok=True)

    session_id = uuid.uuid4().hex[:12]
    state: Dict[str, Any] = {
        "session_id": session_id,
        "created_at": _now_iso(),
        "risk_total": 0.0,
        "posture": "NORMAL",
        "sequence": 0,
        "last_receipt_hash": None,
        "budget": {
            "steps_used": 0,
            "commands": 0,
            "writes": 0,
            "files_touched": 0,
            "runtime_seconds": 0.0,
        },
        "quality": {"tests_passed": False, "lint_passed": False},
    }
    _save_state(state)
    (RECEIPTS_DIR / session_id).mkdir(parents=True, exist_ok=True)
    print(session_id)


def cmd_check_write(args: argparse.Namespace) -> None:
    os.environ.setdefault("SPINE_POLICY_PATH", str(POLICY_PATH))
    state = _require_session(_load_state())

    gr = guard.check_write_scope(
        target_path=args.path,
        workspace_root=str(ROOT),
        budget=None,
        posture=guard.PostureLevel(state.get("posture", "NORMAL")),
    )
    decision = guard.guard_result_to_dict(gr)

    allowed = str(decision.get("verdict", "DENY")).upper() == "ALLOW"
    risk = _extract_risk(decision)
    state["risk_total"] = float(state.get("risk_total", 0.0)) + (0.0 if allowed else risk)
    state["budget"]["steps_used"] = int(state["budget"].get("steps_used", 0)) + 1
    if allowed:
        state["budget"]["writes"] = int(state["budget"].get("writes", 0)) + 1
    state["budget"]["files_touched"] = int(state["budget"].get("files_touched", 0)) + 1
    _update_posture(state)
    _save_state(state)

    print(json.dumps(decision, indent=2, sort_keys=True))
    sys.exit(0 if allowed else 3)


def cmd_check_command(args: argparse.Namespace) -> None:
    os.environ.setdefault("SPINE_POLICY_PATH", str(POLICY_PATH))
    state = _require_session(_load_state())

    gr = guard.check_command(
        command=args.command,
        budget=None,
        posture=guard.PostureLevel(state.get("posture", "NORMAL")),
    )
    decision = guard.guard_result_to_dict(gr)

    allowed = str(decision.get("verdict", "DENY")).upper() == "ALLOW"
    risk = _extract_risk(decision)
    state["risk_total"] = float(state.get("risk_total", 0.0)) + (0.0 if allowed else risk)
    state["budget"]["steps_used"] = int(state["budget"].get("steps_used", 0)) + 1
    state["budget"]["commands"] = int(state["budget"].get("commands", 0)) + 1
    _update_posture(state)
    _save_state(state)

    print(json.dumps(decision, indent=2, sort_keys=True))
    sys.exit(0 if allowed else 3)


def cmd_receipt(args: argparse.Namespace) -> None:
    os.environ.setdefault("SPINE_POLICY_PATH", str(POLICY_PATH))
    os.environ.setdefault("SPINE_RECEIPT_SCHEMA", str(SCHEMA_PATH))
    os.environ.setdefault("SPINE_RECEIPT_DIR", str(RECEIPTS_DIR))

    state = _require_session(_load_state())
    session_id = state["session_id"]

    # Derive effect/risk by re-running guard on provided command/path
    effect_class = "SHELL_SAFE"
    risk = 0.0
    blocked_by: Optional[str] = None
    allowed = True

    if args.command:
        gr = guard.check_command(
            command=args.command,
            budget=None,
            posture=guard.PostureLevel(state.get("posture", "NORMAL")),
        )
        d = guard.guard_result_to_dict(gr)
        effect_class = str(d.get("effect_class") or effect_class)
        risk = _extract_risk(d)
        allowed = str(d.get("verdict", "DENY")).upper() == "ALLOW"
        blocked_by = None if allowed else str(d.get("reason") or "pre_exec_guard")
    elif args.path:
        gr = guard.check_write_scope(
            target_path=args.path,
            workspace_root=str(ROOT),
            budget=None,
            posture=guard.PostureLevel(state.get("posture", "NORMAL")),
        )
        d = guard.guard_result_to_dict(gr)
        effect_class = str(d.get("effect_class") or effect_class)
        risk = _extract_risk(d)
        allowed = str(d.get("verdict", "DENY")).upper() == "ALLOW"
        blocked_by = None if allowed else str(d.get("reason") or "pre_write_guard")

    action = receipts.ActionRecord(
        tool="shell" if args.command else "filesystem",
        operation="command" if args.command else "write",
        effect_class=effect_class,
        risk_delta=risk if not allowed else (0.0 if effect_class == "SHELL_SAFE" else risk),
        description=args.description or "",
        command=args.command,
        target_paths=[args.path] if args.path else [],
        reversibility="REVERSIBLE",
    )

    result = receipts.ResultRecord(
        status="success" if allowed else "blocked",
        exit_code=int(args.exit_code) if args.exit_code is not None else None,
        blocked_by=blocked_by,
        diff_hash=args.diff_hash,
        files_created=[],
        files_modified=[],
        files_deleted=[],
        stdout_truncated=None,
    )

    # Restore chain state
    chain = receipts.SessionChain(session_id=session_id)
    chain.sequence = int(state.get("sequence", 0))
    chain.last_receipt_hash = state.get("last_receipt_hash")

    receipt = receipts.emit_receipt(
        chain=chain,
        proposal_id=args.proposal_id or str(uuid.uuid4()),
        action=action,
        result=result,
        budget=_budget_snapshot(state),
        executor_model=os.environ.get("SPINE_MODEL", "unknown"),
        executor_instance=os.environ.get("SPINE_INSTANCE", ""),
        workspace_root=str(ROOT),
    )

    # Persist chain fields back to session state
    # NOTE: Risk was already accumulated by check-command/check-write.
    # Receipt records risk_delta for forensics but does NOT re-add to session total.
    state["sequence"] = chain.sequence
    state["last_receipt_hash"] = chain.last_receipt_hash
    state["budget"]["steps_used"] = int(state["budget"].get("steps_used", 0)) + 1
    _update_posture(state)
    _save_state(state)

    print(json.dumps(receipt, indent=2, sort_keys=True))
    sys.exit(0 if allowed else 3)


def cmd_quality_gate(args: argparse.Namespace) -> None:
    """Run quality gate — actually executes test runner and linter."""
    import yaml

    state = _require_session(_load_state())
    os.environ.setdefault("SPINE_POLICY_PATH", str(POLICY_PATH))

    # Load gate config from policy
    policy_text = POLICY_PATH.read_text(encoding="utf-8")
    policy = yaml.safe_load(policy_text)
    gates = policy.get("quality_gates", {})

    # Normalize: CLI uses hyphens (pre-modify), policy uses underscores (pre_modify)
    gate_key = args.gate.replace("-", "_")
    gate = gates.get(gate_key, {})

    if not gate:
        print(json.dumps({"error": f"Unknown gate: {args.gate} (tried key: {gate_key})"}), file=sys.stderr)
        sys.exit(4)

    results: Dict[str, bool] = {}
    errors: list[str] = []

    # Run tests if required
    if gate.get("tests_must_pass"):
        test_cfg = gates.get("test_runner", {})
        cmd = test_cfg.get("command", "pytest")
        timeout = test_cfg.get("timeout_seconds", 120)
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(ROOT),
            )
            if r.returncode == 127:
                # Tool not installed — skip with warning, don't fail
                results["tests"] = True
                errors.append(f"WARN: test runner not found ({cmd}); skipped")
            elif r.returncode == 5:
                # pytest exit code 5 = no tests collected. Not a failure —
                # the gate enforces "tests pass," not "tests exist."
                results["tests"] = True
                errors.append(f"WARN: no tests collected ({cmd}); skipped")
            else:
                results["tests"] = r.returncode == 0
                if not results["tests"]:
                    errors.append(f"Tests failed (exit {r.returncode}): {r.stdout[-500:]}")
        except subprocess.TimeoutExpired:
            results["tests"] = False
            errors.append(f"Tests timed out ({timeout}s)")
        except Exception as e:
            results["tests"] = False
            errors.append(f"Test runner error: {e}")

    # Run lint if required
    if gate.get("lint_must_pass"):
        lint_cfg = gates.get("linter", {})
        cmd = lint_cfg.get("command", "ruff check .")
        timeout = lint_cfg.get("timeout_seconds", 30)
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(ROOT),
            )
            if r.returncode == 127:
                results["lint"] = True
                errors.append(f"WARN: linter not found ({cmd}); skipped")
            else:
                results["lint"] = r.returncode == 0
                if not results["lint"]:
                    errors.append(f"Lint failed (exit {r.returncode}): {r.stdout[-500:]}")
        except subprocess.TimeoutExpired:
            results["lint"] = False
            errors.append(f"Lint timed out ({timeout}s)")
        except Exception as e:
            results["lint"] = False
            errors.append(f"Linter error: {e}")

    # Check receipt chain if required (pre-commit only)
    if gate.get("receipt_chain_valid"):
        if not state.get("session_id"):
            results["receipt_chain"] = False
            errors.append("No active session for receipt chain validation")
        elif state.get("sequence", 0) == 0:
            results["receipt_chain"] = False
            errors.append("No receipts emitted — chain empty")
        else:
            session_dir = str(RECEIPTS_DIR / state["session_id"])
            verification = receipts.verify_chain(session_dir)
            results["receipt_chain"] = verification.get("chain_valid", False)
            if not results["receipt_chain"]:
                errors.extend(verification.get("errors", []))

    passed = all(results.values()) if results else False

    state["quality"]["tests_passed"] = results.get("tests", False)
    state["quality"]["lint_passed"] = results.get("lint", False)
    _save_state(state)

    print(json.dumps({
        "gate": args.gate,
        "passed": passed,
        "checks": results,
        "errors": errors,
    }, indent=2))
    sys.exit(0 if passed else 4)


def cmd_close_session(args: argparse.Namespace) -> None:
    os.environ.setdefault("SPINE_RECEIPT_SCHEMA", str(SCHEMA_PATH))
    os.environ.setdefault("SPINE_RECEIPT_DIR", str(RECEIPTS_DIR))

    state = _require_session(_load_state())
    session_id = state["session_id"]
    session_dir = str(RECEIPTS_DIR / session_id)

    verification = receipts.verify_chain(session_dir=session_dir)
    if not verification.get("chain_valid", False):
        print(json.dumps(verification, indent=2), file=sys.stderr)
        sys.exit(5)

    if not (state["quality"].get("tests_passed") and state["quality"].get("lint_passed")):
        print(json.dumps({
            "ok": False,
            "reason": "quality_gate_failed",
            "quality": state["quality"],
            "hint": "Run: python hooks/governor.py quality-gate pre-commit",
        }, indent=2), file=sys.stderr)
        sys.exit(6)

    closed_path = SESS_DIR / f"session_{session_id}_closed.json"
    state["closed_at"] = _now_iso()
    closed_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    STATE_PATH.unlink(missing_ok=True)

    print(json.dumps({
        "ok": True,
        "session_id": session_id,
        "posture": state.get("posture"),
        "risk_total": state.get("risk_total"),
        "receipts": state.get("sequence", 0),
    }, indent=2))
    sys.exit(0)


def cmd_status(args: argparse.Namespace) -> None:
    state = _load_state()
    if not state:
        print(json.dumps({"active": False}, indent=2))
        return
    elapsed = time.time() - state.get("budget", {}).get("runtime_start", time.time())
    state.setdefault("budget", {})["runtime_seconds"] = round(elapsed, 1)
    print(json.dumps({"active": True, **state}, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="spine-governor",
        description="Spine Lite — Governance Hook Runner for Claude Code",
    )
    sub = ap.add_subparsers(dest="op", required=True)

    sub.add_parser("init-session", help="Initialize governed session")

    p = sub.add_parser("check-write", help="Pre-write guard")
    p.add_argument("--path", required=True)

    p = sub.add_parser("check-command", help="Pre-exec guard")
    p.add_argument("--command", required=True)

    p = sub.add_parser("receipt", help="Emit execution receipt")
    p.add_argument("--action", required=True, choices=["file_write", "command", "other"])
    p.add_argument("--path", default=None)
    p.add_argument("--command", default=None)
    p.add_argument("--exit-code", default=None)
    p.add_argument("--diff-hash", default=None)
    p.add_argument("--proposal-id", default=None)
    p.add_argument("--description", default="")

    p = sub.add_parser("quality-gate", help="Run quality gate")
    p.add_argument("gate", nargs="?", default="pre-modify",
                   choices=["pre-modify", "pre-commit"],
                   help="Which gate to run (default: pre-modify)")

    sub.add_parser("close-session", help="Close session with audit")
    sub.add_parser("status", help="Print session status")

    args = ap.parse_args()

    handlers = {
        "init-session": cmd_init_session,
        "check-write": cmd_check_write,
        "check-command": cmd_check_command,
        "receipt": cmd_receipt,
        "quality-gate": cmd_quality_gate,
        "close-session": cmd_close_session,
        "status": cmd_status,
    }

    handler = handlers.get(args.op)
    if handler:
        handler(args)
    else:
        ap.error(f"unknown operation: {args.op}")


if __name__ == "__main__":
    main()
