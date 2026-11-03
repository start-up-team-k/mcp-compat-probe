"""Check MCP tools against the compatibility dataset (measured first, issue-derived second)."""
from __future__ import annotations

import json
import os

from .probes import PROBES, match_probes

# Order = table column order. Ids are stable dataset keys.
CONFIGS = ["anthropic_messages", "anthropic_count_tokens", "openai_chat", "openai_chat_strict",
           "openai_responses", "openai_responses_strict_true", "openai_responses_strict_false"]
# Not measured by this project's workflow (no key); verdicts for these can only be issue-derived. Opt in with --gemini.
GEMINI_CONFIGS = ["gemini_parameters", "gemini_parameters_json_schema"]
DATA_DIR = os.environ.get("MCP_COMPAT_PROBE_DATA") or os.path.join(os.path.dirname(__file__), "data")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_table(data_dir=DATA_DIR):
    """Return {(probe_id, config): entry}. Measured entries win over issue-derived ones."""
    table = {}
    for e in _load(os.path.join(data_dir, "expected-from-issues.json"), {}).get("expectations", []):
        table[(e["probe_id"], e["config"])] = {
            "accepted": e["accepted"], "source": "issue-derived", "url": e["source_url"], "detail": e["quote"]}
    for e in _load(os.path.join(data_dir, "latest.json"), {}).get("results", []):
        if e.get("accepted") is None:
            continue
        table[(e["probe_id"], e["config"])] = {
            "accepted": e["accepted"], "source": "measured " + e["measured_at"][:10],
            "url": None, "detail": e.get("error") or ""}
    return table


def load_tools(obj):
    """Accept a tools array, {"tools": [...]}, or a JSON-RPC response {"result": {"tools": [...]}}."""
    if isinstance(obj, dict) and "result" in obj:
        obj = obj["result"]
    if isinstance(obj, dict):
        obj = obj.get("tools")
    if not isinstance(obj, list):
        raise ValueError("input is not a tools/list result (expected a list of tools or {\"tools\": [...]})")
    return obj


def evaluate(tool, table, configs=CONFIGS):
    """Per config: {"status": FAIL|PASS|UNKNOWN, "source": str, "why": [..]}."""
    ids = ["control"] + match_probes(tool)
    out = {}
    for cfg in configs:
        entries = [(i, table.get((i, cfg))) for i in ids]
        fails = [(i, e) for i, e in entries if e and e["accepted"] is False]
        if fails:
            fails.sort(key=lambda x: x[1]["source"] == "issue-derived")  # measured first
            out[cfg] = {"status": "FAIL", "source": fails[0][1]["source"],
                        "why": [{"probe": i, **e} for i, e in fails]}
        elif all(e for _, e in entries):
            out[cfg] = {"status": "PASS", "source": max(e["source"] for _, e in entries), "why": []}
        else:
            out[cfg] = {"status": "UNKNOWN", "source": "unknown", "why": []}
    return out


def render(results, configs=CONFIGS):
    """results: [(tool_name, evaluation)] -> printable table + findings."""
    def cell(r):
        return {"measured": "%s (%s)" % (r["status"], r["source"].replace("measured ", "")),
                "issue-derived": "%s (issue)" % r["status"]}.get(r["source"].split(" ")[0], r["status"])

    head = ["tool"] + configs
    rows = [[name] + [cell(ev[c]) for c in configs] for name, ev in results]
    widths = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in [head] + rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    notes = []
    for name, ev in results:
        for c in configs:
            for w in ev[c]["why"]:
                src = w["url"] or w["source"]
                notes.append("  %s / %s: probe '%s' rejected [%s] %s" % (
                    name, c, w["probe"], w["source"], ("- " + w["detail"][:160].replace("\n", " ")) if w["detail"] else ""))
                if w["url"]:
                    notes.append("      source: " + src)
    legend = ("Legend: (YYYY-MM-DD) = measured against the live API on that date; (issue) = derived from a public "
              "issue's error text, NOT measured; UNKNOWN = no data (does not fail the check).")
    return "\n".join(lines + ([""] + ["Findings:"] + notes if notes else []) + ["", legend])


def run_check(tools, table, configs=CONFIGS):
    results = [(t.get("name", "?"), evaluate(t, table, configs)) for t in tools]
    failed = any(r[c]["status"] == "FAIL" for _, r in results for c in configs)
    return results, failed
