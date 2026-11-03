# mcp-compat-probe

> Check your MCP server's tool schemas against what the Anthropic and OpenAI APIs actually accept, using a dataset measured against the live APIs.

This is an open-source project built by AI agents as part of an experiment run by start-up-team-k. Feedback and issues are welcome.

## Why

MCP servers emit JSON Schema for every tool. Each model provider accepts a different subset, and one rejected tool can make the whole server unusable before any tool is called. Real examples:

- Top-level `anyOf`/`oneOf`/`allOf` breaks Anthropic- and OpenAI-backed clients ([notion-mcp-server#340](https://github.com/makenotion/notion-mcp-server/issues/340)).
- Gemini rejects tool schemas in n8n ([n8n#15553](https://github.com/n8n-io/n8n/issues/15553)). Gemini is **not measured by this project's workflow** (no key); that report is kept only as an `issue-derived` expectation.

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

Output is a table with one row per tool and one column per measured provider configuration (Anthropic messages, Anthropic `count_tokens`, OpenAI Chat Completions plain and `strict: true`, OpenAI Responses with `strict` omitted / true / false). Gemini columns are hidden by default because Gemini is not measured by this project's workflow; add `--gemini` to show them (issue-derived or `UNKNOWN` only, and an issue-derived Gemini FAIL then affects the exit code). Each cell is `PASS (date)`, `FAIL (date)`, `FAIL (issue)` or `UNKNOWN`. Exit code: `0` nothing failed, `1` at least one FAIL, `2` input error. `UNKNOWN` never fails the check. Add `--json` for machine-readable output.

Run the measurement yourself (needs your own keys; providers without a key are skipped). The project's workflow runs Anthropic and OpenAI only; the Gemini runner code stays in the repo for anyone with a key, but Gemini is not measured by this project's workflow:

```bash
ANTHROPIC_API_KEY=... OPENAI_API_KEY=... python -m mcp_compat_probe probe
```

Default models are the cheapest current model per provider (`claude-haiku-5-5`, `gpt-5-nano`); override with `MCP_COMPAT_ANTHROPIC_MODEL`, `MCP_COMPAT_OPENAI_MODEL`. Optional, unmeasured by the project: set `GEMINI_API_KEY` (model `gemini-3.5-flash-lite`, override `MCP_COMPAT_GEMINI_MODEL`) to also run the Gemini configs yourself. Output tokens are capped at 1 (16 for OpenAI).

OpenAI paths are separate because they behave differently: the Responses API normalizes a schema itself when `strict` is omitted ([docs](https://developers.openai.com/api/docs/guides/function-calling#strict-mode)), so omitted, `true` and `false` are recorded separately there. Chat Completions has two configs (omitted, `true`); `strict: false` is not measured on Chat Completions because it is equivalent to omitting it, which keeps the count at 7 measured configs (plus 2 Gemini configs that are not measured by the project). Every result records `transport: "rest-direct"`.

## Test

Offline, no keys, no network:

```bash
python -m unittest discover -s tests -v
```

## Registry scan

Scan real servers from the [official MCP registry](https://registry.modelcontextprotocol.io) (npm via `npx`, PyPI via `uvx`, stdio, no required env vars), list their tools and judge them in two stages:

```bash
python -m mcp_compat_probe scan registry-scan/targets.json --out registry-scan/YYYY-MM-DD
```

Run it from the `registry-scan` GitHub Actions workflow (manual dispatch, no secrets, read-only token): it executes third-party packages, so it should not run on a personal machine. Results (raw `tools/list` per server plus verdicts in `results.json`) are committed under `registry-scan/YYYY-MM-DD/` and shown as the "Registry scan" table on the Pages site.

- Stage 1, public API: our dataset per measured provider configuration (Anthropic, OpenAI) (FAIL / PASS / UNKNOWN) and where the verdict comes from (`measured` or `issue-derived`). `issue-derived` is NOT a measurement.
- Stage 2, client validation: JSON Schema dialect mismatch that Ajv-style validators reject, e.g. a draft-07 `$schema` against a 2020-12-only client ([typescript-sdk#2677](https://github.com/modelcontextprotocol/typescript-sdk/issues/2677)) or a 2020-12 `$schema` against a draft-07-only client ([claude-task-master#1284](https://github.com/eyaltoledano/claude-task-master/issues/1284)). Judged from the declared `$schema` and 2020-12-only keywords only; no validator is run, so it shows risk for a client class, not a verified failure of a specific client.
- Servers that declare required env vars, or exit with a configuration-looking stderr, are recorded as `needs config` and skipped; a keyword match on stderr is a heuristic.

## Dataset

- `mcp_compat_probe/data/latest.json`: newest measurement per probe and configuration.
- `mcp_compat_probe/data/results/YYYY-MM-DD.json`: every run, including inconclusive results, token usage, and skipped providers (`skipped: no key`).
- `mcp_compat_probe/data/expected-from-issues.json`: expectations copied from public issues with source URL and quote. These are NOT measurements; a measurement overrides them.
- Browse it at https://start-up-team-k.github.io/mcp-compat-probe/ . If no keys have run yet, the page says "No live measurements yet". Gemini cells are "not measured".

Corpus: 45 probes (`mcp_compat_probe/probes.py`), each with one keyword or structure, plus a plain control. Probes are written to satisfy OpenAI strict mode (every object has `additionalProperties: false`, all properties required, a `type` where needed) except for the keyword under test, so a strict rejection points at that keyword; probes that exist to break one of those rules (for example `additional_props_missing`) are the intended exceptions. Nesting is probed at 10 and 11 levels (OpenAI documents a limit of 10) and tool-name length at 65 and 129 characters.

## Security

- The workflow that uses API keys (`.github/workflows/probe.yml`) runs only on `schedule` and `workflow_dispatch` and never uses `pull_request_target`. Its job uses the GitHub Environment `probe`, and keys are passed as environment variables only to the measuring step. The measuring job has read-only repository permissions; a separate job without secrets commits the results.
- Request headers and keys are never printed or stored, and key strings in error bodies are masked (tested offline).
- `ci.yml` (push and pull request) uses no secrets.

## Scope
- Does: ship a probe corpus, measure it on a schedule against live provider APIs, publish dated results, check `tools/list` (file or stdio) against them.
- Does not: fix or rewrite schemas, proxy servers, check client apps (Cursor, etc.), or test tool-call behavior. Only the schema acceptance step of public APIs is measured.

## Limitations

- Each probe tests one construct alone. A tool is judged by the constructs it contains; interactions between constructs and total size limits are not modeled.
- Only Anthropic and OpenAI are measured by the weekly workflow; Gemini verdicts are issue-derived or `UNKNOWN`. An API accepting a schema does not prove the model uses the tool well.
- "Accepted" means HTTP 2xx; "rejected" means HTTP 400/422. Other statuses (rate limits, auth, 5xx) are recorded as inconclusive.
- Requests go straight to the REST endpoints (`transport: "rest-direct"`). Official SDKs can change the schema before sending (for example the `@google/genai` SDK strips unsupported keywords), so an SDK-based client may behave differently from these results.
- The registry scan covers only registry servers that start without configuration; stage 2 is a dialect heuristic, and stage 1 verdicts cover only the measured Anthropic/OpenAI configurations.
- Provider behavior changes over time; check the measurement date. Windows stdio launch of `npx` may need `npx.cmd`.

## License
MIT

<!--
Fund rule: tech-diligence reproduces the project using only this README.
Install, Quick start and Test must work exactly as written.
-->
