"""Minimal MCP stdio client: initialize, then page through tools/list. Standard library only."""
from __future__ import annotations

import collections
import json
import queue
import shutil
import subprocess
import threading
import time


class StdioError(Exception):
    pass


def list_tools(cmd, timeout=90.0):
    exe = shutil.which(cmd[0])
    if exe is None:
        raise StdioError("command not found: %s" % cmd[0])
    proc = subprocess.Popen([exe] + list(cmd[1:]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    lines = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    errtail = collections.deque(maxlen=15)  # last stderr lines, to explain startup failures
    threading.Thread(target=lambda: [errtail.append(l.rstrip()) for l in proc.stderr], daemon=True).start()

    def why(msg):
        return "%s; stderr: %s" % (msg, " | ".join(errtail)[-600:]) if errtail else msg

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:  # a chatty server must not keep us here forever
                raise StdioError(why("timeout waiting for %s" % method))
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                raise StdioError(why("timeout waiting for %s" % method))
            if line is None:
                raise StdioError(why("server exited before answering %s" % method))
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # non-JSON noise on stdout
            if isinstance(msg, dict) and msg.get("id") == id_ and "method" not in msg:
                if "error" in msg:
                    raise StdioError("%s failed: %s" % (method, msg["error"]))
                return msg.get("result") or {}
            # server-initiated requests/notifications: ignored

    try:
        call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "mcp-compat-probe", "version": "0.1.0"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor, i = [], None, 2
        while True:
            res = call(i, "tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            i += 1
            if not cursor:
                return tools
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
