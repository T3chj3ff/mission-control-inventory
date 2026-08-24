#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install the Apple command-line developer tools with: xcode-select --install"
  exit 1
fi

machine="$(scutil --get ComputerName 2>/dev/null || hostname)"
output="$HOME/Desktop/mission-control-projects-mac.json"
if [ "$#" -eq 0 ]; then
  set -- "$HOME/Documents"
fi

python3 - "$output" "$machine" "$@" <<'PY'
import json
import os
import subprocess
import sys
import time

output, machine, *roots = sys.argv[1:]
roots = [os.path.realpath(os.path.expanduser(root)) for root in roots]
for root in roots:
    if not os.path.isdir(root):
        raise SystemExit(f"Not a folder: {root}")

skip = {".git", "node_modules", ".next", ".venv", "venv", "dist", "build", "work", "Library", ".Trash"}
folders = set()
for root in roots:
    for current, dirs, _files in os.walk(root, followlinks=False):
        if ".git" in dirs or os.path.isfile(os.path.join(current, ".git")):
            folders.add(current)
        dirs[:] = [name for name in dirs if name not in skip]

def git(folder, *args):
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={folder}", "-C", folder, *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

def stack_for(folder):
    stack = []
    checks = [
        ("Node / JavaScript", ["package.json"]),
        ("Python", ["pyproject.toml", "requirements.txt"]),
        ("Rust", ["Cargo.toml"]),
        ("Go", ["go.mod"]),
        ("Swift", ["Package.swift"]),
    ]
    names = set(os.listdir(folder))
    for label, markers in checks:
        if any(marker in names for marker in markers):
            stack.append(label)
    if any(name.endswith((".xcodeproj", ".xcworkspace")) for name in names):
        stack.append("Xcode")
    return ", ".join(stack)

projects = []
for folder in sorted(folders, key=str.lower):
    status = git(folder, "status", "--porcelain")
    commit_seconds = git(folder, "log", "-1", "--format=%ct")
    projects.append({
        "name": os.path.basename(folder),
        "summary": "Imported from a local Git working folder",
        "status": "active",
        "machine": machine,
        "localPath": folder,
        "repoUrl": git(folder, "remote", "get-url", "origin"),
        "nextAction": "Review current status and set the next action",
        "gitBranch": git(folder, "rev-parse", "--abbrev-ref", "HEAD"),
        "gitDirty": len(status.splitlines()) if status else 0,
        "lastCommitAt": int(commit_seconds) * 1000 if commit_seconds.isdigit() else None,
        "techStack": stack_for(folder),
    })

payload = {"version": 3, "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "projects": projects}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
print(f"Wrote {len(projects)} projects to {output}")
PY

echo "Return to Mission Control, open Projects, and choose Import inventory."
