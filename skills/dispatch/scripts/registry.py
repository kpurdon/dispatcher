#!/usr/bin/env python3
"""Initiative-scoped agent session registry. Native calls are returned, never executed."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

import backends


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def load(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("expected one JSON object")
    if result.get("isError"):
        raise ValueError("native operation failed; retain its pending request for reconciliation")
    if isinstance(result.get("structuredContent"), dict):
        result = result["structuredContent"]
    elif isinstance(result.get("content"), list):
        blocks = [item["text"] for item in result["content"] if item.get("type") == "text"]
        if len(blocks) == 1:
            result = json.loads(blocks[0])
    if not isinstance(result, dict):
        raise ValueError("expected one JSON object")
    return result


class Registry:
    def __init__(self, path):
        path = Path(path).expanduser()
        self.path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS initiatives (
                id TEXT PRIMARY KEY, lead TEXT NOT NULL, backend TEXT NOT NULL,
                workspace TEXT NOT NULL, record_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY, initiative TEXT NOT NULL REFERENCES initiatives(id),
                backend TEXT NOT NULL, assignment TEXT NOT NULL, status TEXT NOT NULL,
                native TEXT, native_key TEXT, evidence TEXT,
                UNIQUE(backend, native_key)
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, initiative TEXT NOT NULL REFERENCES initiatives(id),
                request_key TEXT NOT NULL, member TEXT NOT NULL REFERENCES members(id),
                action TEXT NOT NULL, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT,
                UNIQUE(initiative, request_key)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_executor_per_assignment
                ON members(initiative,assignment) WHERE status IN ('pending','active');
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def initiative(self, initiative, actor):
        row = self.db.execute("SELECT * FROM initiatives WHERE id=?", (initiative,)).fetchone()
        if row is None:
            raise ValueError("unknown initiative; initialize it explicitly")
        if row["lead"] != actor:
            raise ValueError("actor is not this initiative's recorded lead")
        return dict(row)

    def member(self, initiative, member):
        row = self.db.execute("SELECT * FROM members WHERE id=? AND initiative=?",
                              (member, initiative)).fetchone()
        if row is None:
            raise ValueError("session is not a member of this initiative")
        value = dict(row)
        value["native"] = json.loads(value["native"]) if value["native"] else None
        return value

    def create(self, initiative, actor, backend, workspace, record_path):
        for value, name in ((initiative, "initiative"), (actor, "lead")):
            backends.text(value, name)
        if backend not in backends.BACKENDS:
            raise ValueError("choose a backend explicitly; installed software is not origin evidence")
        workspace = backends.absolute(workspace, "workspace")
        record_path = backends.absolute(record_path, "record_path")
        with self.transaction():
            self.db.execute("INSERT INTO initiatives VALUES (?,?,?,?,?)",
                            (initiative, actor, backend, workspace, record_path))
        return self.initiative(initiative, actor)

    def listing(self, initiative, actor):
        self.initiative(initiative, actor)
        members = []
        for row in self.db.execute("SELECT id FROM members WHERE initiative=? ORDER BY rowid", (initiative,)):
            member = self.member(initiative, row[0])
            member["pending_requests"] = [dict(r) for r in self.db.execute(
                "SELECT id,request_key,action FROM requests WHERE member=? AND status='pending'", (row[0],))]
            members.append(member)
        return members

    def request(self, initiative, actor, request_id):
        self.initiative(initiative, actor)
        row = self.db.execute("SELECT * FROM requests WHERE id=? AND initiative=?", (request_id, initiative)).fetchone()
        if row is None:
            raise ValueError("request is not part of this initiative")
        self.member(initiative, row["member"])
        return {"request_id": request_id, "member_id": row["member"], "status": row["status"],
                "reconcile_only": True, "original": json.loads(row["plan"]),
                "result": json.loads(row["result"]) if row["result"] else None}

    def prepare(self, initiative, actor, request_key, action, *, member=None,
                backend=None, assignment=None, spec=None, message=None, evidence=None):
        backends.text(request_key, "request_key")
        if action not in ("launch", "inspect", "message", "retire"):
            raise ValueError("unknown action")
        intent = {"action": action, "member": member, "backend": backend,
                  "assignment": assignment, "spec": spec, "message": message, "evidence": evidence}
        fingerprint = hashlib.sha256(encode(intent).encode()).hexdigest()
        with self.transaction():
            project = self.initiative(initiative, actor)
            previous = self.db.execute("SELECT * FROM requests WHERE initiative=? AND request_key=?",
                                       (initiative, request_key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("request key was already used for a different operation")
                return {"request_id": previous["id"], "member_id": previous["member"],
                        "status": previous["status"], "reconcile": True,
                        "message": "Existing request: do not execute it again; inspect the backend and complete its receipt."}
            request_id = str(uuid.uuid4())
            if action == "launch":
                backend = backend or project["backend"]
                backends.text(assignment, "assignment")
                backends.launch(backend, spec or {}, request_id)
                member = str(uuid.uuid4())
                self.db.execute("INSERT INTO members (id,initiative,backend,assignment,status) VALUES (?,?,?,?,?)",
                                (member, initiative, backend, assignment, "pending"))
                context = (f"\n\ndispatcher membership (coordination data): initiative={initiative}; "
                           f"member={member}; request={request_id}; lead={actor}; registry={self.path}. "
                           "This assignment does not grant management of other sessions.")
                spec = dict(spec)
                if backend == "t3":
                    spec["message"] = context.strip() + "\n\n" + spec["message"]
                else:
                    brief = Path(spec["brief_file"]).read_text(encoding="utf-8") + context
                    directory = self.path.parent / (self.path.name + ".briefs")
                    directory.mkdir(mode=0o700, exist_ok=True)
                    brief_path = directory / (request_id + ".txt")
                    with os.fdopen(os.open(brief_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                                   "w", encoding="utf-8") as output:
                        output.write(brief)
                    spec["brief_file"] = str(brief_path)
                plan = backends.launch(backend, spec, request_id)
            else:
                session = self.member(initiative, member)
                if session["status"] != "active" or not session["native"]:
                    raise ValueError("session has no active, confirmed backend binding")
                if self.db.execute("SELECT 1 FROM requests WHERE member=? AND status='pending'",
                                   (member,)).fetchone():
                    raise ValueError("session has a pending operation; reconcile it before another")
                if action == "retire":
                    backends.text(evidence, "acceptance/cancellation/transfer evidence")
                plan = backends.operation(session["backend"], session["native"], action, message)
            stored = {"call": plan, "spec": spec, "evidence": evidence}
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)",
                            (request_id, initiative, request_key, member, action,
                             fingerprint, encode(stored), "pending", None))
            return {"request_id": request_id, "member_id": member, "status": "pending", "call": plan}

    def complete(self, initiative, actor, request_id, receipt):
        with self.transaction():
            self.initiative(initiative, actor)
            row = self.db.execute("SELECT * FROM requests WHERE id=? AND initiative=?",
                                  (request_id, initiative)).fetchone()
            if row is None:
                raise ValueError("request is not part of this initiative")
            session = self.member(initiative, row["member"])
            if row["status"] == "complete":
                if row["result"] != encode(receipt):
                    raise ValueError("completed request has a different receipt")
                return session
            stored = json.loads(row["plan"])
            if receipt.get("isError") or receipt.get("error"):
                raise ValueError("native call failed; request remains pending for reconciliation")
            if row["action"] == "launch":
                native = backends.identity(session["backend"], receipt, stored["spec"], row["id"])
                self.db.execute("UPDATE members SET native=?,native_key=?,status='active' WHERE id=?",
                                (encode(native), backends.key(session["backend"], native), session["id"]))
            elif row["action"] == "retire":
                if not backends.verified_retirement(session["backend"], session["native"], receipt):
                    raise ValueError("retirement is not verified for this exact session")
                self.db.execute("UPDATE members SET status='retired',evidence=? WHERE id=?",
                                (stored["evidence"], session["id"]))
            else:
                native = session["native"]
                if session["backend"] == "t3":
                    target = receipt.get("thread", receipt)
                    if target.get("threadId") != native["thread_id"]:
                        raise ValueError("receipt names a different thread")
                elif row["action"] == "inspect" and receipt.get("tab_id") != native["tab_id"]:
                    raise ValueError("receipt names a different tab")
            self.db.execute("UPDATE requests SET status='complete',result=? WHERE id=?",
                            (encode(receipt), request_id))
            return self.member(initiative, session["id"])

    def reconcile(self, initiative, actor, request_id, receipt, evidence):
        """Bind an uncertain iTerm launch to an explicitly verified native tab."""
        backends.text(evidence, "exact launch-context correlation or explicit user selection")
        with self.transaction():
            self.initiative(initiative, actor)
            row = self.db.execute("SELECT * FROM requests WHERE id=? AND initiative=? AND action='launch' AND status='pending'",
                                  (request_id, initiative)).fetchone()
            if row is None:
                raise ValueError("no pending launch in this initiative")
            session = self.member(initiative, row["member"])
            if session["backend"] != "iterm2":
                raise ValueError("T3 reconciliation uses its original tool receipt and request identity")
            native = backends.identity("iterm2", receipt)
            self.db.execute("UPDATE members SET native=?,native_key=?,status='active',evidence=? WHERE id=?",
                            (encode(native), backends.key("iterm2", native), evidence, session["id"]))
            self.db.execute("UPDATE requests SET status='complete',result=? WHERE id=?",
                            (encode({"inspection": receipt, "correlation_evidence": evidence}), request_id))
            return self.member(initiative, session["id"])

    def replay_launch(self, initiative, actor, request_id):
        """T3 alone deduplicates identical launch calls by durable clientRequestId."""
        self.initiative(initiative, actor)
        row = self.db.execute("SELECT * FROM requests WHERE id=? AND initiative=? AND action='launch' AND status='pending'",
                              (request_id, initiative)).fetchone()
        if row is None:
            raise ValueError("no pending launch in this initiative")
        session = self.member(initiative, row["member"])
        if session["backend"] != "t3":
            raise ValueError("iTerm launches cannot be replayed; inspect and reconcile")
        return {"request_id": request_id, "member_id": session["id"], "status": "pending",
                "recovery": "T3 idempotent launch replay; keep every argument unchanged",
                "call": json.loads(row["plan"])["call"]}

    def adopt(self, initiative, actor, backend, assignment, receipt, evidence):
        backends.text(evidence, "explicit adoption authority and identity evidence")
        backends.text(assignment, "assignment")
        native = backends.identity(backend, receipt)
        with self.transaction():
            self.initiative(initiative, actor)
            member = str(uuid.uuid4())
            self.db.execute("INSERT INTO members VALUES (?,?,?,?,?,?,?,?)",
                            (member, initiative, backend, assignment, "active", encode(native),
                             backends.key(backend, native), evidence))
            return self.member(initiative, member)

    def bind_codex(self, initiative, actor, member, thread_id, evidence):
        """Only a verified receiver identity; never match titles to infer membership."""
        backends.text(thread_id, "codex_thread_id")
        backends.text(evidence, "receiver identity evidence")
        with self.transaction():
            self.initiative(initiative, actor)
            session = self.member(initiative, member)
            if session["backend"] != "iterm2" or session["status"] != "active":
                raise ValueError("only an active iTerm member can bind a Codex thread")
            native = session["native"]
            if native.get("codex_thread_id") not in (None, thread_id):
                raise ValueError("receiver already has a different Codex identity")
            for row in self.db.execute("SELECT native FROM members WHERE backend='iterm2' AND native IS NOT NULL"):
                if json.loads(row[0]).get("codex_thread_id") == thread_id and native.get("codex_thread_id") != thread_id:
                    raise ValueError("Codex thread already belongs to another member")
            native["codex_thread_id"] = thread_id
            self.db.execute("UPDATE members SET native=?,evidence=? WHERE id=?", (encode(native), evidence, member))
            return self.member(initiative, member)

    def cancel(self, initiative, actor, request_id, evidence):
        """Only after the caller verifies no effect; uncertainty is not cancellation."""
        backends.text(evidence, "verified non-dispatch/no-effect evidence")
        with self.transaction():
            self.initiative(initiative, actor)
            row = self.db.execute("SELECT * FROM requests WHERE id=? AND initiative=? AND status='pending'",
                                  (request_id, initiative)).fetchone()
            if row is None:
                raise ValueError("no pending request in this initiative")
            if row["action"] == "launch":
                self.db.execute("UPDATE members SET status='cancelled',evidence=? WHERE id=?", (evidence, row["member"]))
            self.db.execute("UPDATE requests SET status='cancelled',result=? WHERE id=?",
                            (encode({"no_effect_evidence": evidence}), request_id))
            return {"request_id": request_id, "status": "cancelled"}

    def transfer(self, initiative, actor, member, successor_initiative, successor, evidence):
        backends.text(evidence, "stopped writer and accepted successor obligations")
        with self.transaction():
            self.initiative(initiative, actor)
            self.initiative(successor_initiative, successor)
            session = self.member(initiative, member)
            if session["status"] != "active" or self.db.execute(
                    "SELECT 1 FROM requests WHERE member=? AND status='pending'", (member,)).fetchone():
                raise ValueError("transfer requires an active member with no pending operations")
            self.db.execute("UPDATE members SET initiative=?,evidence=? WHERE id=?",
                            (successor_initiative, evidence, member))
            return self.member(successor_initiative, member)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True, help="one private local registry shared by initiatives")
    parser.add_argument("--initiative", required=True)
    parser.add_argument("--actor", required=True, help="verified lead session identity, not a title")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init")
    create.add_argument("--backend", choices=backends.BACKENDS, required=True)
    create.add_argument("--workspace", required=True)
    create.add_argument("--record-path", required=True)
    commands.add_parser("list")
    request = commands.add_parser("request", help="read a durable request for reconciliation, not redispatch")
    request.add_argument("--request-id", required=True)
    replay = commands.add_parser("replay-launch", help="recover a lost T3 reply using its original idempotent launch")
    replay.add_argument("--request-id", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--request-key", required=True)
    prepare.add_argument("--action", choices=("launch", "inspect", "message", "retire"), required=True)
    prepare.add_argument("--member")
    prepare.add_argument("--backend", choices=backends.BACKENDS)
    prepare.add_argument("--assignment")
    prepare.add_argument("--spec-file")
    prepare.add_argument("--message-file")
    prepare.add_argument("--evidence")
    complete = commands.add_parser("complete")
    complete.add_argument("--request-id", required=True)
    complete.add_argument("--result-file", required=True)
    reconcile = commands.add_parser("reconcile", help="bind an uncertain iTerm launch from exact inspected identity")
    reconcile.add_argument("--request-id", required=True)
    reconcile.add_argument("--result-file", required=True)
    reconcile.add_argument("--evidence", required=True)
    adopt = commands.add_parser("adopt")
    adopt.add_argument("--backend", choices=backends.BACKENDS, required=True)
    adopt.add_argument("--assignment", required=True)
    adopt.add_argument("--result-file", required=True)
    adopt.add_argument("--evidence", required=True)
    bind = commands.add_parser("bind-codex")
    bind.add_argument("--member", required=True)
    bind.add_argument("--thread-id", required=True)
    bind.add_argument("--evidence", required=True)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("--request-id", required=True)
    cancel.add_argument("--no-effect-evidence", required=True)
    transfer = commands.add_parser("transfer")
    transfer.add_argument("--member", required=True)
    transfer.add_argument("--to-initiative", required=True)
    transfer.add_argument("--successor", required=True)
    transfer.add_argument("--evidence", required=True)
    args = parser.parse_args(argv)
    try:
        registry = Registry(args.registry)
        common = (args.initiative, args.actor)
        if args.command == "init":
            result = registry.create(*common, args.backend, args.workspace, args.record_path)
        elif args.command == "list":
            result = registry.listing(*common)
        elif args.command == "request":
            result = registry.request(*common, args.request_id)
        elif args.command == "replay-launch":
            result = registry.replay_launch(*common, args.request_id)
        elif args.command == "prepare":
            result = registry.prepare(*common, args.request_key, args.action, member=args.member,
                backend=args.backend, assignment=args.assignment,
                spec=load(args.spec_file) if args.spec_file else None,
                message=Path(args.message_file).read_text() if args.message_file else None, evidence=args.evidence)
        elif args.command == "complete":
            result = registry.complete(*common, args.request_id, load(args.result_file))
        elif args.command == "reconcile":
            result = registry.reconcile(*common, args.request_id, load(args.result_file), args.evidence)
        elif args.command == "adopt":
            result = registry.adopt(*common, args.backend, args.assignment, load(args.result_file), args.evidence)
        elif args.command == "bind-codex":
            result = registry.bind_codex(*common, args.member, args.thread_id, args.evidence)
        elif args.command == "cancel":
            result = registry.cancel(*common, args.request_id, args.no_effect_evidence)
        else:
            result = registry.transfer(*common, args.member, args.to_initiative, args.successor, args.evidence)
        print(encode(result))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(f"dispatch: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
