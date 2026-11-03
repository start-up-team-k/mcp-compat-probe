# Contributing

Issues and pull requests are welcome. This project is built by AI agents; humans review everything.

- Add a probe: append one entry to `_RAW` in `mcp_compat_probe/probes.py` (one keyword or structure per probe,
  plus a detector). `python -m unittest discover -s tests` must pass.
- Report a provider behavior change: open an issue with the provider, model, and the error text.
- Never commit API keys. Never add a workflow that uses secrets on `pull_request` or `pull_request_target`.
- Standard library only; keep it that way unless there is a strong reason.
