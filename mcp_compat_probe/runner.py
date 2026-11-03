"""Send each probe to real provider APIs and record whether the tool schema was accepted.

Safety rules: request headers are never printed or stored; keys are masked in every error text.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .check import DATA_DIR
from .probes import PROBES

# Default models = cheapest current model on each provider's official docs (checked 2026-11-03).
# Override with the env vars below.
CONFIGS = {
    "anthropic_messages": dict(key="ANTHROPIC_API_KEY", model_env="MCP_COMPAT_ANTHROPIC_MODEL",
                               model="claude-haiku-5-5", endpoint="POST /v1/messages",
                               api_version="anthropic-version: 2023-06-01"),
    "anthropic_count_tokens": dict(key="ANTHROPIC_API_KEY", model_env="MCP_COMPAT_ANTHROPIC_MODEL",
                                   model="claude-haiku-5-5", endpoint="POST /v1/messages/count_tokens",
                                   api_version="anthropic-version: 2023-06-01"),
    "openai_chat": dict(key="OPENAI_API_KEY", model_env="MCP_COMPAT_OPENAI_MODEL", model="gpt-5-nano",
                        endpoint="POST /v1/chat/completions (strict omitted)", api_version="v1"),
    "openai_chat_strict": dict(key="OPENAI_API_KEY", model_env="MCP_COMPAT_OPENAI_MODEL", model="gpt-5-nano",
                               endpoint="POST /v1/chat/completions (strict: true)", api_version="v1"),
    # Responses API normalizes the schema itself when `strict` is omitted, so omitted/true/false are separate paths.
    "openai_responses": dict(key="OPENAI_API_KEY", model_env="MCP_COMPAT_OPENAI_MODEL", model="gpt-5-nano",
                             endpoint="POST /v1/responses (strict omitted)", api_version="v1"),
    "openai_responses_strict_true": dict(key="OPENAI_API_KEY", model_env="MCP_COMPAT_OPENAI_MODEL",
                                         model="gpt-5-nano", endpoint="POST /v1/responses (strict: true)",
                                         api_version="v1"),
    "openai_responses_strict_false": dict(key="OPENAI_API_KEY", model_env="MCP_COMPAT_OPENAI_MODEL",
                                          model="gpt-5-nano", endpoint="POST /v1/responses (strict: false)",
                                          api_version="v1"),
    "gemini_parameters": dict(key="GEMINI_API_KEY", model_env="MCP_COMPAT_GEMINI_MODEL",
                              model="gemini-3.5-flash-lite", endpoint="POST models/{model}:generateContent (parameters)",
                              api_version="v1beta"),
    "gemini_parameters_json_schema": dict(key="GEMINI_API_KEY", model_env="MCP_COMPAT_GEMINI_MODEL",
                                          model="gemini-3.5-flash-lite",
                                          endpoint="POST models/{model}:generateContent (parametersJsonSchema)",
                                          api_version="v1beta"),
}
_KEY_SHAPES = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,})")


def mask(text, secrets):
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return _KEY_SHAPES.sub("***", text)


def build(config, model, key, tool):
    """Return (url, headers, body) for one probe. Keys only ever go into headers."""
    name, desc, schema = tool["name"], tool["description"], tool["inputSchema"]
    if config.startswith("anthropic"):
        h = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": model, "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"name": name, "description": desc, "input_schema": schema}]}
        if config == "anthropic_messages":
            body["max_tokens"] = 1
            return "https://api.anthropic.com/v1/messages", h, body
        return "https://api.anthropic.com/v1/messages/count_tokens", h, body
    if config.startswith("openai"):
        h = {"authorization": "Bearer " + key, "content-type": "application/json"}
        if config.startswith("openai_responses"):
            tool_def = {"type": "function", "name": name, "description": desc, "parameters": schema}
            if config.endswith("strict_true"):
                tool_def["strict"] = True
            elif config.endswith("strict_false"):
                tool_def["strict"] = False
            return ("https://api.openai.com/v1/responses", h,
                    {"model": model, "input": "hi", "max_output_tokens": 16, "tools": [tool_def]})
        fn = {"name": name, "description": desc, "parameters": schema}
        if config == "openai_chat_strict":
            fn["strict"] = True
        return ("https://api.openai.com/v1/chat/completions", h,
                {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 16,
                 "tools": [{"type": "function", "function": fn}]})
    field = "parametersJsonSchema" if config.endswith("json_schema") else "parameters"
    return ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
            {"x-goog-api-key": key, "content-type": "application/json"},
            {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
             "tools": [{"functionDeclarations": [{"name": name, "description": desc, field: schema}]}],
             "generationConfig": {"maxOutputTokens": 1}})


def post(url, headers, body, timeout=60):
    """Return (status, text). Never raises for HTTP errors."""
    req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _usage(config, js):
    u = js.get("usage") or js.get("usageMetadata") or {}
    if config == "anthropic_count_tokens":
        return {"input_tokens": js.get("input_tokens")}
    if config.startswith("gemini"):
        return {"input_tokens": u.get("promptTokenCount"), "output_tokens": u.get("candidatesTokenCount")}
    if config.startswith("openai"):
        if "prompt_tokens" not in u:  # Responses API field names
            return {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
        return {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}
    return {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}


def measure(config, probe, env, sleep=time.sleep):
    spec = CONFIGS[config]
    key = env[spec["key"]]
    model = env.get(spec["model_env"]) or spec["model"]
    url, headers, body = build(config, model, key, probe["tool"])
    status, text, err = None, "", None
    for attempt in range(4):
        try:
            status, text = post(url, headers, body)
        except Exception as e:  # network errors: message may echo the URL, so mask it
            status, text, err = None, "", "%s: %s" % (type(e).__name__, e)
        if status is not None and status != 429 and status < 500:
            break
        sleep(5 * (attempt + 1))
    err = mask(err or (text if status is None or status >= 300 else ""), [key])[:2000] or None
    try:
        js = json.loads(text) if text else {}
    except ValueError:
        js = {}
    ok = status is not None and 200 <= status < 300
    return {"probe_id": probe["id"], "config": config, "model": model,
            "model_returned": js.get("model") or js.get("modelVersion") if isinstance(js, dict) else None,
            "transport": "rest-direct", "endpoint": spec["endpoint"], "api_version": spec["api_version"],
            # 400/422 = the API looked at the schema and refused it; anything else is inconclusive.
            "accepted": True if ok else (False if status in (400, 422) else None),
            "http_status": status, "error": err if not ok else None,
            "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usage": _usage(config, js) if ok and isinstance(js, dict) else None}


def run_all(env=None, only=None, sleep=time.sleep, log=print):
    env = os.environ if env is None else env
    results, skipped = [], []
    probes = [p for p in PROBES if not only or p["id"] in only or p["id"] == "control"]
    for config, spec in CONFIGS.items():
        if not env.get(spec["key"]):
            skipped.append({"config": config, "reason": "skipped: no key"})
            log("%s: skipped: no key" % config)
            continue
        got = []
        for p in probes:
            r = measure(config, p, env, sleep)
            got.append(r)
            if r["http_status"] in (401, 403):
                skipped.append({"config": config, "reason": "skipped: auth failed (HTTP %s)" % r["http_status"]})
                break
        results += got
        n = lambda a: sum(1 for r in got if r["accepted"] is a)
        log("%s: %d accepted, %d rejected, %d inconclusive" % (config, n(True), n(False), n(None)))
    return results, skipped


def valid_configs(results):
    """Configs whose control probe was accepted; a failed control means the run says nothing."""
    return {r["config"] for r in results if r["probe_id"] == "control" and r["accepted"] is True}


def save(results, skipped, data_dir=DATA_DIR):
    now = datetime.now(timezone.utc)
    os.makedirs(os.path.join(data_dir, "results"), exist_ok=True)
    doc = {"schema_version": 1, "date": now.strftime("%Y-%m-%d"), "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "results": results, "skipped": skipped}
    with open(os.path.join(data_dir, "results", doc["date"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    latest_path = os.path.join(data_dir, "latest.json")
    try:
        with open(latest_path, encoding="utf-8") as f:
            latest = json.load(f)
    except FileNotFoundError:
        latest = {"schema_version": 1, "results": []}
    good = valid_configs(results)
    merged = {(r["probe_id"], r["config"]): r for r in latest.get("results", [])}
    merged.update({(r["probe_id"], r["config"]): r for r in results
                   if r["config"] in good and r["accepted"] is not None})
    latest.update(generated_at=doc["generated_at"], results=list(merged.values()))
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, indent=1)
    return doc
