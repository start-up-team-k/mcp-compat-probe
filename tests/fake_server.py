"""Tiny fake MCP stdio server used by tests (two pages of tools)."""
import json
import sys

PAGES = {None: ([{"name": "a", "inputSchema": {"type": "object", "anyOf": [{}]}}], "p2"),
         "p2": ([{"name": "b", "inputSchema": {"type": "object", "properties": {}}}], None)}
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0"}}
    else:
        tools, nxt = PAGES[m["params"].get("cursor")]
        res = {"tools": tools, **({"nextCursor": nxt} if nxt else {})}
    print("not json noise", flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}), flush=True)
