#!/usr/bin/env python3
"""
entry.py — Claude Code hooks adapter for Spine Lite governance pack.

This script is invoked by Claude Code command hooks. It reads the hook event JSON on stdin,
routes the event to hooks/governor.py, and (for PreToolUse) blocks disallowed tool calls
via exit code 2 (stderr fed back to Claude as error context).

Enforcement mechanism:
- Exit 0  = tool call allowed (default)
- Exit 2  = tool call BLOCKED (hard gate — tool never executes)

We use exit code 2 exclusively for denials. The JSON permissionDecision approach
(exit 0 + stdout JSON) has known reliability bugs in Claude Code where denials
are silently ignored. Exit code 2 is the kernel boundary.

Design goals:
- Fail-closed on unknown / unparseable inputs for Bash + Write/Edit tools.
- Every allowed or blocked attempt gets a forensic receipt.
- SessionStart initializes governance state; SessionEnd closes + audits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
RUNNER = ROOT / "hooks" / "governor.py"

def _run_runner(args: list[str]) -> Tuple[int, str, str]:
    """Run the hook runner and return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, str(RUNNER)] + args
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def _deny(reason: str) -> None:
    """Block a tool call using exit code 2 (hard gate).

    Per Claude Code docs: exit 2 = blocking error. stderr is fed back to Claude.
    This is the reliable enforcement path — JSON permissionDecision has known
    bugs where denials are silently ignored (GitHub issues #4669, #21988).
    Exit code 2 is the kernel boundary. Tools cannot execute past it.
    """
    sys.stderr.write(f"[SPINE] BLOCKED: {reason[:500]}\n")
    sys.exit(2)

def _best_effort_json(s: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(s)
    except Exception:
        return None

def _extract_path(tool_input: Dict[str, Any]) -> Optional[str]:
    for k in ("file_path", "path", "filepath", "target_path", "filename"):
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def _extract_command(tool_input: Dict[str, Any]) -> Optional[str]:
    v = tool_input.get("command")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None

def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _deny("Hook input was not valid JSON; governance is fail-closed.")

    event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        if event == "PreToolUse":
            _deny("Missing session_id in hook input; cannot enforce governance.")
        session_id = "unknown"

    if not RUNNER.exists():
        if event == "PreToolUse":
            _deny("Governance runner missing; tool call blocked.")
        sys.exit(0)

    # Session lifecycle
    if event == "SessionStart":
        _run_runner(["init-session"])
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.exit(0)

    if event == "SessionEnd":
        _run_runner(["close-session"])
        out = {"hookSpecificOutput": {"hookEventName": "SessionEnd"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.exit(0)

    # Tool interception
    if event == "PreToolUse":
        if tool_name == "Bash":
            command = _extract_command(tool_input)
            if not command:
                _deny("Bash tool_input.command missing; cannot classify. Blocked (fail-closed).")
            rc, out, err = _run_runner(["check-command", "--command", command])
            decision = _best_effort_json(out) or {}
            allowed = (rc == 0)
            if not allowed:
                _run_runner(["receipt", "--action", "command", "--command", command, "--proposal-id", "hook", "--description", "blocked by PreToolUse hook"])
                reason = str(decision.get("reason") or err or "Command blocked by governance.")
                _deny(reason)
            sys.exit(0)

        if tool_name in ("Write", "Edit"):
            path = _extract_path(tool_input)
            if not path:
                _deny(f"{tool_name} tool_input.file_path missing; cannot enforce scope. Blocked (fail-closed).")
            rc, out, err = _run_runner(["check-write", "--path", path])
            decision = _best_effort_json(out) or {}
            allowed = (rc == 0)
            if not allowed:
                _run_runner(["receipt", "--action", "file_write", "--path", path, "--proposal-id", "hook", "--description", f"{tool_name} blocked by PreToolUse hook"])
                reason = str(decision.get("reason") or err or "Write blocked by governance.")
                _deny(reason)
            sys.exit(0)

        sys.exit(0)

    if event == "PostToolUse":
        exit_code = None
        tool_output = payload.get("tool_output")
        if isinstance(tool_output, dict):
            ec = tool_output.get("exit_code") or tool_output.get("exitCode")
            if isinstance(ec, int):
                exit_code = str(ec)

        if tool_name == "Bash":
            command = _extract_command(tool_input) or ""
            args = ["receipt", "--action", "command", "--command", command, "--proposal-id", "hook", "--description", "PostToolUse receipt"]
            if exit_code is not None:
                args += ["--exit-code", exit_code]
            _run_runner(args)
            sys.exit(0)

        if tool_name in ("Write", "Edit"):
            path = _extract_path(tool_input) or ""
            args = ["receipt", "--action", "file_write", "--path", path, "--proposal-id", "hook", "--description", f"PostToolUse receipt ({tool_name})"]
            _run_runner(args)
            sys.exit(0)

        sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
