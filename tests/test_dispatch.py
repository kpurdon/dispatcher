"""Scope, concurrency, and native-result contracts; no live sessions are launched."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/dispatch/scripts"
sys.path.insert(0, str(SCRIPTS))
import backends
from registry import Registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private/registry.sqlite3"
        self.r = Registry(self.path)
        self.addCleanup(self.r.db.close)
        self.r.create("jobs", "lead-a", "t3", "/workspace", "/private/jobs.md")
        self.r.create("routing", "lead-b", "iterm2", "/workspace", "/private/routing.md")
        self.spec = {"title": "Cancellation", "environment_id": "env-a", "project_id": "app",
                     "workspace_strategy": {"type": "worktree", "baseRef": "origin/main", "startFromOrigin": True},
                     "message": "Investigate then implement the assigned contract."}

    def launch(self, key="launch-1", assignment="Cancellation"):
        return self.r.prepare("jobs", "lead-a", key, "launch", assignment=assignment, spec=self.spec)

    def active(self):
        pending = self.launch()
        self.r.complete("jobs", "lead-a", pending["request_id"], self.t3_receipt(pending))
        return pending["member_id"]

    def t3_receipt(self, request):
        return {"threadId": "thread-a", "projectId": "app", "verification": {
            "thread": {"threadId": "thread-a", "projectId": "app"},
            "items": [{"type": "user_message", "text": f"Dispatcher membership: request={request['request_id']};"}]}}

    def test_scope_blocks_other_leads_and_other_initiative_members(self):
        member = self.active()
        self.assertEqual(self.r.listing("routing", "lead-b"), [])
        for run in (lambda: self.r.listing("jobs", "lead-b"),
                    lambda: self.r.prepare("routing", "lead-b", "x", "inspect", member=member),
                    lambda: self.r.prepare("routing", "lead-b", "y", "retire", member=member, evidence="merged")):
            with self.assertRaises(ValueError):
                run()

    def test_request_replay_returns_no_call_and_different_intent_is_rejected(self):
        first = self.launch()
        replay = self.launch()
        self.assertEqual(first["member_id"], replay["member_id"])
        self.assertNotIn("call", replay)
        self.assertTrue(replay["reconcile"])
        with self.assertRaises(ValueError):
            self.launch(assignment="Unrelated")
        with self.assertRaises(sqlite3.IntegrityError):
            self.launch(key="bypass")
        self.assertEqual(len(self.r.listing("jobs", "lead-a")), 1)

    def test_simultaneous_launch_reservations_emit_one_call(self):
        def reserve(_):
            r = Registry(self.path)
            try:
                return r.prepare("jobs", "lead-a", "same-request", "launch", assignment="one", spec=self.spec)
            finally:
                r.db.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(4)))
        self.assertEqual(sum("call" in r for r in results), 1)
        self.assertEqual(len({r["member_id"] for r in results}), 1)

    def test_t3_lost_reply_replay_keeps_exact_native_idempotency_and_member(self):
        request = self.launch()
        replay = self.r.replay_launch("jobs", "lead-a", request["request_id"])
        self.assertEqual(replay["call"], request["call"])
        self.assertEqual(replay["member_id"], request["member_id"])
        self.assertEqual(replay["call"]["arguments"]["clientRequestId"], request["request_id"])
        with self.assertRaises(ValueError):
            self.r.replay_launch("routing", "lead-b", request["request_id"])
        self.r.complete("jobs", "lead-a", request["request_id"], self.t3_receipt(request))
        with self.assertRaises(ValueError):
            self.r.replay_launch("jobs", "lead-a", request["request_id"])

    def test_failed_or_mismatched_launch_receipt_retains_original_reservation(self):
        request = self.launch()
        for receipt in ({"isError": True}, {"threadId": "thread", "projectId": "other"}, {}):
            with self.assertRaises(ValueError):
                self.r.complete("jobs", "lead-a", request["request_id"], receipt)
        self.assertEqual(self.r.member("jobs", request["member_id"])["status"], "pending")
        self.assertNotIn("call", self.launch())
        with self.assertRaises(ValueError):
            self.r.complete("jobs", "lead-a", request["request_id"], self.t3_receipt({"request_id": "unrelated"}))
        self.r.complete("jobs", "lead-a", request["request_id"], self.t3_receipt(request))
        self.assertEqual(self.r.member("jobs", request["member_id"])["status"], "active")

    def test_inspection_and_messages_keep_recorded_destination(self):
        member = self.active()
        request = self.r.prepare("jobs", "lead-a", "inspect", "inspect", member=member)
        args = request["call"]["arguments"]
        self.assertEqual((args["environmentId"], args["threadId"]), ("env-a", "thread-a"))
        with self.assertRaises(ValueError):
            self.r.complete("jobs", "lead-a", request["request_id"], {"thread": {"threadId": "other"}})
        self.r.complete("jobs", "lead-a", request["request_id"], {"thread": {"threadId": "thread-a"}})
        message = self.r.prepare("jobs", "lead-a", "message", "message", member=member, message="Continue")
        self.assertEqual(message["call"]["arguments"]["mode"], "queue")
        self.assertEqual(message["call"]["arguments"]["threadId"], "thread-a")

    def test_one_initiative_spans_repository_projects_without_importing_other_sessions(self):
        app = self.active()
        infra_spec = {**self.spec, "project_id": "terraform", "title": "Infrastructure"}
        request = self.r.prepare("jobs", "lead-a", "infra", "launch", assignment="Infrastructure", spec=infra_spec)
        receipt = {"threadId": "infra-thread", "projectId": "terraform", "verification": {
            "thread": {"threadId": "infra-thread", "projectId": "terraform"},
            "items": [{"type": "user_message", "text": f"request={request['request_id']};"}]}}
        self.r.complete("jobs", "lead-a", request["request_id"], receipt)
        members = self.r.listing("jobs", "lead-a")
        self.assertEqual({m["native"]["project_id"] for m in members}, {"app", "terraform"})
        self.assertEqual({m["id"] for m in members}, {app, request["member_id"]})
        self.assertEqual(self.r.listing("routing", "lead-b"), [])

    def test_retirement_needs_evidence_exact_readback_and_no_new_calls_while_pending(self):
        member = self.active()
        with self.assertRaises(ValueError):
            self.r.prepare("jobs", "lead-a", "retire", "retire", member=member)
        request = self.r.prepare("jobs", "lead-a", "retire", "retire", member=member, evidence="PR acceptance verified")
        for data in ({"threadId": "thread-a", "projectId": "app", "archived": False},
                     {"threadId": "other", "projectId": "app", "archived": True},
                     {"threadId": "thread-a", "projectId": "other", "archived": True}):
            with self.assertRaises(ValueError):
                self.r.complete("jobs", "lead-a", request["request_id"], {"thread": data})
        with self.assertRaises(ValueError):
            self.r.prepare("jobs", "lead-a", "extra", "inspect", member=member)
        self.r.complete("jobs", "lead-a", request["request_id"],
                        {"thread": {"threadId": "thread-a", "projectId": "app", "archived": True}})
        self.assertEqual(self.r.member("jobs", member)["status"], "retired")
        with self.assertRaises(ValueError):
            self.r.prepare("jobs", "lead-a", "after", "message", member=member, message="write")

    def test_adoption_is_explicit_inspected_and_unique_across_initiatives(self):
        receipt = {"environment_id": "env-a", "thread": {"threadId": "adopted", "projectId": "infra", "archived": False}}
        for evidence, result in (("", receipt), ("user selected thread", {"environment_id": "env-a", "threadId": "adopted", "projectId": "infra"})):
            with self.assertRaises(ValueError):
                self.r.adopt("jobs", "lead-a", "t3", "Infra", result, evidence)
        member = self.r.adopt("jobs", "lead-a", "t3", "Infra", receipt, "user selected and inspected thread")
        with self.assertRaises(sqlite3.IntegrityError):
            self.r.adopt("routing", "lead-b", "t3", "Steal", receipt, "discovered same title")
        self.assertEqual(member["backend"], "t3")
        self.assertEqual(self.r.listing("routing", "lead-b"), [])

    def test_transfer_requires_named_successor_and_cannot_move_pending_work(self):
        member = self.active()
        with self.assertRaises(ValueError):
            self.r.transfer("jobs", "lead-a", member, "routing", "wrong-lead", "accepted")
        request = self.r.prepare("jobs", "lead-a", "check", "inspect", member=member)
        with self.assertRaises(ValueError):
            self.r.transfer("jobs", "lead-a", member, "routing", "lead-b", "accepted")
        self.r.cancel("jobs", "lead-a", request["request_id"], "inspection not dispatched")
        result = self.r.transfer("jobs", "lead-a", member, "routing", "lead-b", "writer stopped; lead-b accepted all obligations")
        self.assertEqual(result["native"]["thread_id"], "thread-a")
        with self.assertRaises(ValueError):
            self.r.prepare("jobs", "lead-a", "old-owner", "inspect", member=member)
        self.assertEqual(self.r.listing("routing", "lead-b")[0]["id"], member)

    def test_backend_override_is_per_member_and_never_rewrites_initiative_default(self):
        brief = Path(self.tmp.name) / "brief.txt"
        brief.write_text("Implement the fix.")
        spec = {"title": "CLI fix", "cwd": "/worktree", "brief_file": str(brief), "group_id": "group"}
        request = self.r.prepare("jobs", "lead-a", "cli", "launch", backend="iterm2", assignment="CLI", spec=spec)
        self.r.complete("jobs", "lead-a", request["request_id"],
                        {"launch": "accepted", "request_id": request["request_id"], "tab_id": "1", "terminal_session_id": "pane", "window_id": "window"})
        self.assertEqual(self.r.initiative("jobs", "lead-a")["backend"], "t3")
        self.assertEqual(self.r.member("jobs", request["member_id"])["backend"], "iterm2")
        saved = self.r.request("jobs", "lead-a", request["request_id"])["original"]["spec"]["brief_file"]
        self.assertIn(request["member_id"], Path(saved).read_text())
        self.assertEqual(Path(saved).stat().st_mode & 0o777, 0o600)
        self.assertEqual(brief.read_text(), "Implement the fix.")
        self.assertNotIn("--force", backends.operation("iterm2", self.r.member("jobs", request["member_id"])["native"], "retire")["argv"])

    def test_iterm_receipts_correlate_and_uncertain_requests_reconcile_without_relaunch(self):
        brief = Path(self.tmp.name) / "brief.txt"
        brief.write_text("Implement cancellation.")
        spec = {"title": "CLI", "cwd": "/worktree", "brief_file": str(brief), "group_id": "group"}
        request = self.r.prepare("jobs", "lead-a", "cli", "launch", backend="iterm2", assignment="CLI", spec=spec)
        wrong = {"launch": "accepted", "request_id": "another-request", "tab_id": "1",
                 "terminal_session_id": "pane", "window_id": "window"}
        with self.assertRaises(ValueError):
            self.r.complete("jobs", "lead-a", request["request_id"], wrong)
        with self.assertRaises(ValueError):
            self.r.replay_launch("jobs", "lead-a", request["request_id"])
        self.assertEqual(self.r.member("jobs", request["member_id"])["status"], "pending")
        inspection = {"tab_id": "1", "present": True, "window_id": "window", "sessions": [{"session_id": "pane"}]}
        with self.assertRaises(ValueError):
            self.r.reconcile("jobs", "lead-a", request["request_id"], inspection, "")
        member = self.r.reconcile("jobs", "lead-a", request["request_id"], inspection,
                                  "native receiver's first prompt contains this request/member; exact pane verified")
        self.assertEqual(member["native"]["tab_id"], "1")
        retire = self.r.prepare("jobs", "lead-a", "retire-cli", "retire", member=member["id"], evidence="accepted")
        with self.assertRaises(ValueError):
            self.r.complete("jobs", "lead-a", retire["request_id"], {"tab_id": "other", "present": False})
        retired = self.r.complete("jobs", "lead-a", retire["request_id"], {"tab_id": "1", "present": False})
        self.assertEqual(retired["status"], "retired")

    def test_cli_roundtrip_creates_private_registry_without_native_calls(self):
        script = SCRIPTS / "registry.py"
        path = Path(self.tmp.name) / "cli.sqlite3"
        args = [sys.executable, str(script), "--registry", str(path), "--initiative", "one", "--actor", "caller"]
        subprocess.run([*args, "init", "--backend", "iterm2", "--workspace", "/workspace", "--record-path", "/private/one.md"],
                       capture_output=True, text=True, check=True)
        result = subprocess.run([*args, "list"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
