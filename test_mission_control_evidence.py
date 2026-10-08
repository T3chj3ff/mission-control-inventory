import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mission_control_evidence import collect, scoped_files, run_json


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / "source.py").write_text("def action(): pass\n", encoding="utf-8")
        self.identity = {"commit": "a" * 40, "branch": "main", "dirty": False}
        self.status = {"index": {"state": "complete", "checkout": {"commit": "a" * 40},
            "published_generation": 1, "completed_at_ms": 1700000000000,
            "files": 1, "symbols": 1, "edges": 0, "stale_extractions": 0,
            "drift": {"checked": 1, "modified": 0, "deleted": 0, "unreadable": 0, "omitted": 0}},
            "this_build": {"version": "0.1.0", "commit": "b" * 40, "dirty": False,
                           "executable_blake3": "c" * 64}}

    def response(self, binary, root, args):
        if args[0] == "status":
            return copy.deepcopy(self.status)
        if args[0] == "languages":
            return {"languages": [{"language": "python", "remaining_gaps": ["dynamic dispatch"]}]}
        return {"files": [{"path": "source.py", "index_freshness": "Current"}],
                "matched_attempts": 2, "prior_attempts": [{"secret": "must not export"}]}

    def capture(self):
        with patch("mission_control_evidence.git", return_value=str(self.root)), \
             patch("mission_control_evidence.checkout", return_value=self.identity), \
             patch("mission_control_evidence.run_json", side_effect=self.response):
            return collect(self.root, "repograph", ["source.py"])

    def test_metadata_only_with_hash_and_no_check_execution(self):
        snapshot = self.capture()
        self.assertEqual(snapshot["scope"][0]["sha256"], hashlib.sha256((self.root / "source.py").read_bytes()).hexdigest())
        self.assertEqual(snapshot["contexts"][0]["matchedAttempts"], 2)
        self.assertEqual(snapshot["checks"]["status"], "not-run")
        serialized = json.dumps(snapshot)
        self.assertNotIn("must not export", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("def action", serialized)

    def test_stale_capture_is_explicit(self):
        self.status["index"]["drift"]["modified"] = 1
        self.assertTrue(any("stale or incomplete" in warning for warning in self.capture()["warnings"]))

    def test_incomplete_index_and_missing_drift_fail_closed(self):
        self.status["index"]["state"] = "not_indexed"
        with self.assertRaises(ValueError): self.capture()
        self.status["index"]["state"] = "complete"
        self.status["index"]["drift"] = None
        with self.assertRaises(ValueError): self.capture()

    def test_scope_paths_and_limits(self):
        for names in [["../outside.py"], ["missing.py"], ["source.py"] * 21]:
            with self.assertRaises(ValueError): scoped_files(self.root, names)
        self.assertEqual(len(scoped_files(self.root, ["source.py", "source.py"])), 1)

    def test_changes_during_capture_are_rejected(self):
        calls = 0
        def responses(binary, root, args):
            nonlocal calls
            calls += 1
            if args[0] == "context": (self.root / "source.py").write_text("changed", encoding="utf-8")
            return self.response(binary, root, args)
        with patch("mission_control_evidence.git", return_value=str(self.root)), \
             patch("mission_control_evidence.checkout", return_value=self.identity), \
             patch("mission_control_evidence.run_json", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "changed during capture"):
                collect(self.root, "repograph", ["source.py"])
        self.assertGreater(calls, 2)

    def test_cli_failure_does_not_expose_raw_stderr(self):
        with patch("mission_control_evidence.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, "exit 1"):
                run_json("repograph", self.root, ["status", "--json"])


if __name__ == "__main__": unittest.main()
