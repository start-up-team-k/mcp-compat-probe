"""Registry scan: list tools of many stdio servers, check them, and judge two stages.

Stage 1 (public API): our dataset, per provider configuration (FAIL/PASS/UNKNOWN + source).
Stage 2 (client validation): JSON Schema dialect mismatch that Ajv-style validators reject.
  Evidence: typescript-sdk#2677 (servers declare draft-07; strict 2020-12 clients reject the tool list) and
  claude-task-master#1284 (server declares 2020-12; a draft-07-only client fails to start).
  Standard-library judgement only: the declared `$schema` and 2020-12-only keywords. No validator is run.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

from . import check, stdio
from .probes import nodes

D07, D2020 = "draft-07", "2020-12"
ONLY_2020 = ("prefixItems", "unevaluatedProperties", "unevaluatedItems", "dependentSchemas", "dependentRequired",
             "$dynamicRef", "$dynamicAnchor", "minContains", "maxContains")
NEEDS_CONFIG = re.compile(r"api[_ -]?key|token|credential|secret|not set|missing|required|env|auth|login|config",
                          re.I)


def dialect(schema):
    s = schema.get("$schema") if isinstance(schema, dict) else None
    if not isinstance(s, str):
        return None
    if "2020-12" in s:
        return D2020
    if "draft-07" in s or "draft-06" in s or "draft-04" in s:
        return D07
    return "other"


def client_stage(tool):
    """None if nothing to flag, else {tool, dialect, keywords, risk}."""
    schema = tool.get("inputSchema") or {}
    d = dialect(schema)
    kws = sorted({k for n in nodes(schema) for k in ONLY_2020 if k in n})
    if d == D07:
        return {"tool": tool.get("name"), "dialect": d, "keywords": ["$schema=draft-07"] + kws,
                "risk": "rejected by 2020-12-only validators (typescript-sdk#2677)"
                        + ("; also uses 2020-12-only keywords" if kws else "")}
    if d == D2020:
        return {"tool": tool.get("name"), "dialect": d, "keywords": ["$schema=2020-12"],
                "risk": "rejected by draft-07-only validators (claude-task-master#1284)"}
    if d == "other":
        return {"tool": tool.get("name"), "dialect": d, "keywords": ["$schema=" + str(schema["$schema"])[:80]],
                "risk": "unknown $schema value; Ajv fails with 'no schema with key or ref'"}
    return None


def judge(tools, table):
    """Both stages for one server's tools. Returns (api_stage, client_stage)."""
    results, _ = check.run_check(tools, table)
    api = {c: {"FAIL": 0, "PASS": 0, "UNKNOWN": 0, "sources": []} for c in check.CONFIGS}
    fails = []
    for name, ev in results:
        for c in check.CONFIGS:
            api[c][ev[c]["status"]] += 1
            if ev[c]["status"] == "FAIL":
                kind = "measured" if ev[c]["source"].startswith("measured") else "issue-derived"
                if kind not in api[c]["sources"]:
                    api[c]["sources"].append(kind)
                fails.append({"tool": name, "config": c, "keywords": [w["probe"] for w in ev[c]["why"]
                                                                       if w["probe"] != "control"] or ["control"],
                              "source": kind})
    flagged = [x for x in map(client_stage, tools) if x]
    return {"configs": api, "failures": fails}, {"flagged": flagged,
            "dialects": sorted({x["dialect"] for x in flagged})}


def scan_one(target, out_dir, table, timeout=90, lister=stdio.list_tools):
    """Run one target. Never raises; status in {ok, needs config, exec failed}."""
    rec = {k: target.get(k) for k in ("name", "repo", "registry_type", "package", "version", "stars")}
    rec["command"] = target["command"]
    if target.get("env"):
        rec.update(status="needs config", error="registry declares required env: " + ", ".join(target["env"]))
        return rec
    try:
        tools = lister(target["command"], timeout=timeout)
    except Exception as e:
        msg = str(e)[:700]
        stderr = msg.split("stderr:", 1)[-1] if "stderr:" in msg else ""
        rec.update(status="needs config" if NEEDS_CONFIG.search(stderr) else "exec failed", error=msg)
        return rec
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", target["name"])
    with open(os.path.join(out_dir, slug + ".tools.json"), "w", encoding="utf-8") as f:
        json.dump({"tools": tools}, f, indent=1)
    api, client = judge(tools, table)
    rec.update(status="ok", tools_file=slug + ".tools.json", n_tools=len(tools), api_stage=api, client_stage=client)
    return rec


def run_scan(targets_path, out_dir, data_dir=check.DATA_DIR, timeout=90, lister=stdio.list_tools, log=print):
    with open(targets_path, encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    os.makedirs(out_dir, exist_ok=True)
    table = check.load_table(data_dir)
    servers = []
    for t in targets:
        r = scan_one(t, out_dir, table, timeout, lister)
        log("%-55s %s%s" % (t["name"], r["status"], " (%d tools)" % r["n_tools"] if r["status"] == "ok" else ""))
        servers.append(r)
    doc = {"schema_version": 1, "date": os.path.basename(out_dir.rstrip("/")),
           "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "measured_data_present": bool(check._load(os.path.join(data_dir, "latest.json"), {}).get("results")),
           "servers": servers}
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    return doc
