#!/usr/bin/env python3
"""Mission Control local command runner.

The runner polls an owner-approved Mission Control queue, starts commands only
in locally configured roots, and returns bounded stdout/stderr. It has no
inbound port and never accepts commands directly from the network. The root
check is a working-directory guardrail, not an operating-system sandbox.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any
from urllib import error, request

DEFAULT_SITE = "https://mission-control-flight-deck.techj3ff.chatgpt.site"
CONFIG_DIR = Path.home() / ".mission-control"
CONFIG_FILE = CONFIG_DIR / "agent.json"
MAX_OUTPUT = 200_000
BLOCKED_PATTERNS = [
    r"\brm\b(?=[^\n]*(?:-[a-z]*r|--recursive))",
    r"\bremove-item\b(?=[^\n]*-recurse)",
    r"\bformat(?:\.com)?\b",
    r"\bmkfs(?:\.|\s)",
    r"\bdiskpart\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[^\n]*f",
    r"\breg\s+delete\b",
    r"\bdel\b(?=[^\n]*\/s)",
    r"\b(?:powershell|pwsh)\b[^\n]*-(?:encodedcommand|enc)\b",
]


class AgentError(RuntimeError):
    pass


def configure(config_path: Path) -> None:
    print("Mission Control secure runner configuration")
    site_url = input(f"Mission Control URL [{DEFAULT_SITE}]: ").strip() or DEFAULT_SITE
    agent_token = getpass.getpass("Machine token (hidden): ").strip()
    if not agent_token.startswith("mc_agent_"):
        raise AgentError("That does not look like a Mission Control machine token.")
    print("Private Sites need a separate Sites connection token. Leave blank if your Site does not require one.")
    site_token = getpass.getpass("Sites connection token (hidden, optional): ").strip()
    default_root = str(Path.home() / "Documents")
    raw_roots = input(f"Allowed roots, separated by commas [{default_root}]: ").strip() or default_root
    roots = [str(Path(value.strip()).expanduser().resolve()) for value in raw_roots.split(",") if value.strip()]
    missing = [root for root in roots if not Path(root).is_dir()]
    if missing:
        raise AgentError(f"These allowed roots do not exist: {', '.join(missing)}")
    config = {
        "site_url": site_url.rstrip("/"),
        "agent_token": agent_token,
        "site_token": site_token,
        "allowed_roots": roots,
        "poll_seconds": 4,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass
    print(f"Configuration saved to {config_path}")
    print("Start the runner with: python3 mission_control_agent.py run")


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise AgentError(f"Configuration not found: {config_path}. Run configure first.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for key in ("site_url", "agent_token", "allowed_roots"):
        if not config.get(key):
            raise AgentError(f"Configuration is missing {key}.")
    return config


def api(config: dict[str, Any], method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {config['agent_token']}",
        "Accept": "application/json",
        "User-Agent": f"MissionControlAgent/1.0 ({platform.system()})",
    }
    site_token = config.get("site_token", "")
    if site_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {site_token}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    target = f"{config['site_url']}/api/agent"
    try:
        with request.urlopen(request.Request(target, data=body, headers=headers, method=method), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403) and not site_token:
            raise AgentError("The private Site rejected this identity-less connection. Add a Sites connection token with configure.") from exc
        raise AgentError(f"Mission Control returned HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise AgentError(f"Could not reach Mission Control: {exc.reason}") from exc


def inside_allowed_root(directory: str, roots: list[str]) -> Path:
    cwd = Path(directory).expanduser().resolve()
    if not cwd.is_dir():
        raise AgentError(f"Working directory does not exist: {cwd}")
    cwd_key = os.path.normcase(str(cwd))
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        try:
            if os.path.commonpath([cwd_key, os.path.normcase(str(root_path))]) == os.path.normcase(str(root_path)):
                return cwd
        except ValueError:
            continue
    raise AgentError(f"Working directory is outside locally allowed roots: {cwd}")


def guard_command(command: str) -> None:
    normalized = command.lower()
    if any(re.search(pattern, normalized) for pattern in BLOCKED_PATTERNS):
        raise AgentError("The local safety guard rejected a destructive command pattern.")
    if len(command) > 8_000:
        raise AgentError("Command exceeds the 8,000-character local limit.")


def shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    shell = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/bash"
    return [shell, "-lc", command]


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def drain(stream: Any, target: queue.Queue[tuple[str, str]], label: str) -> None:
    try:
        for chunk in iter(stream.readline, ""):
            target.put((label, chunk))
    finally:
        stream.close()


def execute(config: dict[str, Any], job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    process: subprocess.Popen[str] | None = None
    cancelled = False
    error_message = ""
    exit_code: int | None = None
    try:
        cwd = inside_allowed_root(str(job["workingDirectory"]), list(config["allowed_roots"]))
        command = str(job["command"])
        guard_command(command)
        print(f"[{job_id[:8]}] Starting {job.get('title', 'command')} in {cwd}")
        process = subprocess.Popen(
            shell_command(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        chunks: queue.Queue[tuple[str, str]] = queue.Queue()
        threading.Thread(target=drain, args=(process.stdout, chunks, "stdout"), daemon=True).start()
        threading.Thread(target=drain, args=(process.stderr, chunks, "stderr"), daemon=True).start()
        started = time.monotonic()
        last_update = 0.0
        timeout_seconds = max(10, min(1800, int(job.get("timeoutSeconds", 900))))
        while process.poll() is None:
            collect(chunks, stdout_parts, stderr_parts)
            now = time.monotonic()
            if now - last_update >= 2:
                response = api(config, "POST", {"action": "progress", "jobId": job_id, "stdout": bounded(stdout_parts), "stderr": bounded(stderr_parts)})
                last_update = now
                if response.get("cancelRequested"):
                    cancelled = True
                    stop_process(process)
                    break
            if now - started > timeout_seconds:
                error_message = f"Local runner stopped the command after {timeout_seconds} seconds."
                stop_process(process)
                break
            time.sleep(0.2)
        collect(chunks, stdout_parts, stderr_parts)
        exit_code = process.poll()
        if exit_code is None:
            exit_code = 124 if error_message else 130
    except Exception as exc:  # Report local refusal/runtime faults to the command ledger.
        error_message = str(exc)
        exit_code = 126
        if process is not None:
            stop_process(process)
    finally:
        api(config, "POST", {"action": "complete", "jobId": job_id, "exitCode": exit_code, "stdout": bounded(stdout_parts), "stderr": bounded(stderr_parts), "errorMessage": error_message, "cancelled": cancelled})
        print(f"[{job_id[:8]}] Finished with exit code {exit_code}")


def collect(chunks: queue.Queue[tuple[str, str]], stdout_parts: list[str], stderr_parts: list[str]) -> None:
    while True:
        try:
            label, chunk = chunks.get_nowait()
        except queue.Empty:
            break
        (stdout_parts if label == "stdout" else stderr_parts).append(chunk)


def bounded(parts: list[str]) -> str:
    return "".join(parts)[-MAX_OUTPUT:]


def run(config_path: Path, once: bool = False) -> None:
    config = load_config(config_path)
    roots = [str(Path(root).expanduser().resolve()) for root in config["allowed_roots"]]
    config["allowed_roots"] = roots
    print(f"Mission Control runner online. Allowed roots: {', '.join(roots)}")
    failures = 0
    while True:
        try:
            response = api(config)
            failures = 0
            job = response.get("job")
            if job:
                execute(config, job)
            elif once:
                return
            time.sleep(max(1, min(30, int(response.get("pollAfterSeconds", config.get("poll_seconds", 4))))))
        except KeyboardInterrupt:
            print("Runner stopped.")
            return
        except AgentError as exc:
            failures += 1
            print(f"Connection warning: {exc}", file=sys.stderr)
            if once:
                raise
            time.sleep(min(30, 2 ** min(failures, 5)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Secure local runner for Mission Control")
    parser.add_argument("action", choices=("configure", "run", "once"))
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    args = parser.parse_args()
    try:
        if args.action == "configure":
            configure(args.config)
        else:
            run(args.config, once=args.action == "once")
        return 0
    except (AgentError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
