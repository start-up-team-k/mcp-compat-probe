# mcp-compat-probe

> Check your MCP server's tool schemas against what Anthropic, OpenAI and Gemini APIs actually accept, using a dataset measured against the live APIs.

This is an open-source project built by AI agents as part of an experiment run by start-up-team-k. Feedback and issues are welcome.

## Why

MCP servers emit JSON Schema for every tool. Each model provider accepts a different subset, and one rejected tool can make the whole server unusable before any tool is called. Real examples:

- Top-level `anyOf`/`oneOf`/`allOf` breaks Anthropic- and OpenAI-backed clients ([notion-mcp-server#340](https://github.com/makenotion/notion-mcp-server/issues/340)).
- Gemini rejects tool schemas in n8n ([n8n#15553](https://github.com/n8n-io/n8n/issues/15553)).

Existing linters (e.g. mcpdialect) use hand-written static rules. This project instead keeps a corpus of minimal probe tools, sends each one to the real provider APIs on a weekly schedule, and commits the results with date, model and raw error text. The CLI checks your `tools/list` against that dataset. Every verdict says where it comes from: `measured YYYY-MM-DD`, `issue-derived` (taken from a public bug report, not measured), or `unknown`.

## Install

Requires Python 3.9+ and nothing else (standard library only).

```bash
pip install git+https://github.com/start-up-team-k/mcp-compat-probe
```

Or from a clone: `git clone https://github.com/start-up-team-k/mcp-compat-probe && cd mcp-compat-probe` and use `python -m mcp_compat_probe ...` below.

## Quick start

From a saved `tools/list` result (a tools array, `{"tools": [...]}`, or a JSON-RPC response):

```bash
python -m mcp_compat_probe check examples/tools-list.json
```

From a live stdio server (everything after `--` is the server command; needs Node for this example):

```bash
python -m mcp_compat_probe check -- npx -y @modelcontextprotocol/server-everything
```

Output is a table with one row per tool and one column per provider configuration (Anthropic messages, Anthropic `count_tokens`, OpenAI, OpenAI strict, Gemini `parameters`, Gemini `parametersJsonSchema`). Each cell is `PASS (date)`, `FAIL (date)`, `FAIL (issue)` or `UNKNOWN`. Exit code: `0` nothing failed, `1` at least one FAIL, `2` input error. `UNKNOWN` never fails the check. Add `--json` for machine-readable output.

Run the measurement yourself (needs your own keys; providers without a key are skipped):

```bash
ANTHROPIC_API_KEY=... OPENAI_API_KEY=... GEMINI_API_KEY=... python -m mcp_compat_probe probe
```

Default models are the cheapest current model per provider (`claude-haiku-5-5`, `gpt-5-nano`, `gemini-3.5-flash-lite`); override with `MCP_COMPAT_ANTHROPIC_MODEL`, `MCP_COMPAT_OPENAI_MODEL`, `MCP_COMPAT_GEMINI_MODEL`. Output tokens are capped at 1 (16 for OpenAI).

## Test

Offline, no keys, no network:

```bash
python -m unittest discover -s tests -v
```

## Dataset

- `mcp_compat_probe/data/latest.json`: newest measurement per probe and configuration.
- `mcp_compat_probe/data/results/YYYY-MM-DD.json`: every run, including inconclusive results, token usage, and skipped providers (`skipped: no key`).
- `mcp_compat_probe/data/expected-from-issues.json`: expectations copied from public issues with source URL and quote. These are NOT measurements; a measurement overrides them.
- Browse it at https://start-up-team-k.github.io/mcp-compat-probe/ . If no keys have run yet, the page says "No live measurements yet".

Corpus: 43 probes (`mcp_compat_probe/probes.py`), each with one keyword or structure, plus a plain control.

## Security

- The workflow that uses API keys (`.github/workflows/probe.yml`) runs only on `schedule` and `workflow_dispatch` and never uses `pull_request_target`. Its job uses the GitHub Environment `probe`, and keys are passed as environment variables only to the measuring step. The measuring job has read-only repository permissions; a separate job without secrets commits the results.
- Request headers and keys are never printed or stored, and key strings in error bodies are masked (tested offline).
- `ci.yml` (push and pull request) uses no secrets.

## Scope
- Does: ship a probe corpus, measure it on a schedule against live provider APIs, publish dated results, check `tools/list` (file or stdio) against them.
- Does not: fix or rewrite schemas, proxy servers, check client apps (Cursor, etc.), or test tool-call behavior. Only the schema acceptance step of public APIs is measured.

## Limitations

- Each probe tests one construct alone. A tool is judged by the constructs it contains; interactions between constructs and total size limits are not modeled.
- The measured set is empty until API keys are configured for the weekly workflow; until then only issue-derived expectations exist. An API accepting a schema does not prove the model uses the tool well.
- "Accepted" means HTTP 2xx; "rejected" means HTTP 400/422. Other statuses (rate limits, auth, 5xx) are recorded as inconclusive.
- Provider behavior changes over time; check the measurement date. Windows stdio launch of `npx` may need `npx.cmd`.

## License
MIT

<!--
Fund rule: tech-diligence reproduces the project using only this README.
Install, Quick start and Test must work exactly as written.
-->
