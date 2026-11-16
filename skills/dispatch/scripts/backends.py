"""Build explicit native calls; no backend discovery or side effects at import time."""

from pathlib import Path


BACKENDS = ("iterm2", "t3")
TABS = Path(__file__).resolve().parents[2] / "tabs/scripts/tabs.py"


def text(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be nonempty text without control characters")
    return value


def absolute(value, label):
    value = text(value, label)
    if not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return value


def command(*args):
    return {"transport": "command", "argv": ["uv", "run", "--quiet", "--script", str(TABS), *args]}


def tool(name, **arguments):
    return {"transport": "tool", "name": name, "arguments": arguments}


def launch(backend, spec, request_id):
    """A launch specification is supplied after verifying its destination."""
    text(spec.get("title"), "title")
    if backend == "iterm2":
        cwd = absolute(spec.get("cwd"), "cwd")
        brief = absolute(spec.get("brief_file"), "brief_file")
        origins = [key for key in ("group_id", "origin_session", "window_id", "codex_origin", "desktop")
                   if spec.get(key)]
        if len(origins) != 1:
            raise ValueError("select exactly one verified iTerm destination or origin adapter")
        for flag in ("desktop", "codex_origin"):
            if flag in spec and not isinstance(spec[flag], bool):
                raise ValueError(f"{flag} must be a boolean")
        args = ["open", "--cwd", cwd, "--title", spec["title"], "--append-file", brief,
                "--request-id", request_id]
        for key in origins:
            args.append("--" + key.replace("_", "-"))
            if key not in ("codex_origin", "desktop"):
                args.append(text(spec[key], key))
        if spec.get("group"):
            if spec.get("group_id"):
                raise ValueError("group and group_id cannot be combined")
            args.extend(["--group", text(spec["group"], "group")])
        return command(*args, "--", "codex", "--")
    if backend != "t3":
        raise ValueError("unsupported backend")
    environment = text(spec.get("environment_id"), "environment_id")
    project = text(spec.get("project_id"), "project_id")
    strategy = spec.get("workspace_strategy")
    if not isinstance(strategy, dict) or strategy.get("type") not in ("root", "worktree", "existing_worktree"):
        raise ValueError("select root, worktree, or existing_worktree explicitly")
    if strategy["type"] == "worktree":
        text(strategy.get("baseRef"), "baseRef")
        if not isinstance(strategy.get("startFromOrigin"), bool):
            raise ValueError("worktree requires explicit startFromOrigin")
    if strategy["type"] == "existing_worktree":
        absolute(strategy.get("worktreePath"), "worktreePath")
    message = spec.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 120000:
        raise ValueError("a T3 launch requires 1-120000 characters in message")
    return tool("t3_thread_launch", environmentId=environment, projectId=project,
                title=spec["title"], workspaceStrategy=strategy, message=message,
                clientRequestId=request_id)


def identity(backend, receipt, spec=None, request_id=None):
    """Extract identity from an actual native launch or inspection result."""
    if backend == "iterm2":
        if spec is not None and receipt.get("launch") != "accepted":
            raise ValueError("launch was not accepted; reconcile its existing request")
        if spec is not None and receipt.get("request_id") != request_id:
            raise ValueError("launch receipt belongs to a different request")
        if spec is None:
            sessions = receipt.get("sessions", [])
            if receipt.get("present") is not True or len(sessions) != 1:
                raise ValueError("adoption needs inspection of an existing tab with exactly one pane")
            receipt = {**receipt, "terminal_session_id": sessions[0].get("session_id")}
        return {key: text(receipt.get(key), key)
                for key in ("tab_id", "terminal_session_id", "window_id")}
    if backend != "t3":
        raise ValueError("unsupported backend")
    data = receipt.get("thread", receipt)
    if spec is None and ("thread" not in receipt or data.get("archived") is not False):
        raise ValueError("adoption needs inspection of an unarchived T3 thread")
    result = {"thread_id": text(data.get("threadId"), "threadId"),
              "project_id": text(data.get("projectId"), "projectId"),
              "environment_id": text((spec or receipt).get("environment_id"), "environment_id")}
    if spec and result["project_id"] != spec["project_id"]:
        raise ValueError("launch returned a different project")
    if spec is not None:
        verification = receipt.get("verification", {})
        thread = verification.get("thread", {})
        marker = f"request={request_id};"
        if (thread.get("threadId") != result["thread_id"]
                or thread.get("projectId") != result["project_id"]
                or not any(item.get("type") == "user_message" and marker in (item.get("text") or "")
                           for item in verification.get("items", []))):
            raise ValueError("launch needs native thread readback containing this request's initial context")
    return result


def key(backend, native):
    if backend == "iterm2":
        return native["tab_id"]
    return native["environment_id"] + ":" + native["thread_id"]


def operation(backend, native, action, message=None):
    if backend == "t3":
        args = {"environmentId": native["environment_id"], "threadId": native["thread_id"]}
        read = tool("t3_thread_read", **args, limit=3, runLimit=1, maxCharsPerItem=2000)
        if action == "inspect":
            return read
        if action == "message":
            if not isinstance(message, str) or not message.strip():
                raise ValueError("message is required")
            return tool("t3_thread_send", **args, message=message, mode="queue")
        if action == "retire":
            return {**tool("t3_thread_organize", **args, action="archive"), "verify": read}
    elif backend == "iterm2":
        if action == "inspect":
            return command("inspect", "--tab-id", native["tab_id"])
        if action == "retire":
            return command("close", "--tab-id", native["tab_id"],
                           "--only-session", native["terminal_session_id"])
        if action == "message":
            thread = native.get("codex_thread_id")
            if not thread:
                raise ValueError("record the receiver's verified codex_thread_id before messaging")
            if not isinstance(message, str) or not message.strip() or len(message.encode()) > 1000:
                raise ValueError("Codex messages need 1-1000 UTF-8 bytes; reference a brief file for longer assignments")
            return tool("send_message_to_thread", threadId=thread, prompt=message)
    raise ValueError("unsupported backend operation")


def verified_retirement(backend, native, receipt):
    if backend == "iterm2":
        return (receipt.get("tab_id") == native["tab_id"]
                and (receipt.get("close") == "confirmed" or receipt.get("present") is False))
    data = receipt.get("thread", {})
    return (data.get("threadId") == native["thread_id"]
            and data.get("projectId") == native["project_id"] and data.get("archived") is True)
