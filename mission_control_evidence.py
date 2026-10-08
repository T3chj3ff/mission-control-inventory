#!/usr/bin/env python3
"""Export a bounded RepoGraph snapshot locally. Never uploads or executes checks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

MAX_BYTES = 2_000_000


def run_json(binary: str, root: Path, args: list[str]) -> dict:
    # Disk-backed capture avoids keeping arbitrary CLI output in memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run([binary, *args], cwd=root, stdout=output, stderr=errors,
                                timeout=90, check=False)
        if result.returncode:
            raise ValueError(f"RepoGraph {args[0]} failed (exit {result.returncode}); run it locally for details.")
        output.seek(0)
        data = output.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("RepoGraph response exceeds the export limit; narrow the scope.")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Unexpected RepoGraph response.")
        return value


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, timeout=30, check=True)
    return result.stdout.strip()


def checkout(root: Path) -> dict:
    return {"commit": git(root, "rev-parse", "HEAD"),
            "branch": git(root, "branch", "--show-current"),
            "dirty": bool(git(root, "status", "--porcelain"))}


def scoped_files(root: Path, names: list[str]) -> list[dict]:
    if len(names) > 20:
        raise ValueError("Select at most 20 files.")
    result = []
    for name in dict.fromkeys(names):
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("Every scope file must exist inside the repository.")
        if candidate.stat().st_size > MAX_BYTES:
            raise ValueError("A scope file exceeds the hashing limit.")
        result.append({"path": candidate.relative_to(root).as_posix(),
                       "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()})
    return result


def collect(root: Path, binary: str, files: list[str]) -> dict:
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Choose the Git repository root.")
    before = checkout(root)
    scope = scoped_files(root, files)
    status = run_json(binary, root, ["status", "--json"])
    languages = run_json(binary, root, ["languages", "--json"])
    index = status.get("index")
    if not isinstance(index, dict) or index.get("state") != "complete":
        raise ValueError("A complete RepoGraph index is required. Run repograph index locally first.")
    indexed_commit = (index.get("checkout") or {}).get("commit")
    drift = index.get("drift")
    if not isinstance(drift, dict):
        raise ValueError("RepoGraph did not provide an on-disk drift check.")
    contexts = []
    for entry in scope:
        context = run_json(binary, root, ["context", "--file", entry["path"], "--json"])
        entry["indexFreshness"] = next((f.get("index_freshness", "Unknown")
            for f in context.get("files", []) if f.get("path") == entry["path"]), "Unknown")
        # Deliberately exclude prose, commands, source text and test outcomes.
        contexts.append({"path": entry["path"], "matchedAttempts": context.get("matched_attempts", 0),
                         "truncated": bool(context.get("truncated"))})
    after = checkout(root)
    after_status = run_json(binary, root, ["status", "--json"])
    if before != after or scoped_files(root, files) != [{k: v for k, v in f.items()
            if k != "indexFreshness"} for f in scope] or index != after_status.get("index"):
        raise ValueError("Repository or index changed during capture. Retry after changes settle.")
    warnings = ["Imported evidence is a point-in-time snapshot, not a live view.",
                "Static relationships do not prove execution or change safety.",
                "Untracked, ignored and unsupported files can fall outside indexed drift checks.",
                "Dirty state is not a full worktree fingerprint; verify scoped hashes before editing."]
    stale = bool(indexed_commit != before["commit"] or index.get("stale_extractions")
                 or any(drift.get(k, 0) for k in ("modified", "deleted", "unreadable", "omitted"))
                 or any(f["indexFreshness"] != "Current" for f in scope))
    if stale:
        warnings.append("Index evidence is stale or incomplete. Reindex and export again before relying on it.")
    return {"schemaVersion": 1, "kind": "mission-control-repograph",
            "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checkout": before,
            "index": {"state": "complete", "generation": index["published_generation"],
                      "commit": indexed_commit, "completedAt": datetime.fromtimestamp(
                          index["completed_at_ms"] / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
                      "files": index["files"], "symbols": index["symbols"], "edges": index["edges"],
                      "staleExtractions": index.get("stale_extractions", 0),
                      "drift": {k: drift.get(k, 0) for k in ("checked", "modified", "deleted", "unreadable", "omitted")}},
            "build": {k: (status.get("this_build") or {}).get(k)
                      for k in ("version", "commit", "dirty", "executable_blake3")},
            "languages": [{"language": lang["language"], "completeSemantics": bool(lang.get("complete_language_semantics")),
                           "gaps": lang.get("remaining_gaps", [])} for lang in languages.get("languages", [])],
            "scope": scope, "contexts": contexts, "warnings": warnings,
            "checks": {"status": "not-run", "reason": "Evidence export does not execute or certify checks."}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--repograph", default="repograph", help="Installed CLI name or explicit executable path")
    parser.add_argument("--file", action="append", default=[], help="Repository-relative scope file (repeatable)")
    parser.add_argument("--output", required=True, type=Path, help="New local JSON file; existing files are refused")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Output already exists; choose a new snapshot filename.")
        binary = shutil.which(args.repograph)
        if not binary:
            raise ValueError("RepoGraph executable not found; install it or provide --repograph.")
        snapshot = collect(args.root, str(Path(binary).resolve()), args.file)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(snapshot, output, indent=2, ensure_ascii=False)
            output.write("\n")
        print(f"Saved evidence to {args.output}. Review it before importing into Mission Control.")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Evidence export failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
