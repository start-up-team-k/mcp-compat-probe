from __future__ import annotations

import argparse
import json
import sys

from . import check, runner, scan, stdio


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:  # everything after -- is the stdio server command
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="mcp-compat-probe")
    sub = ap.add_subparsers(dest="sub", required=True)
    c = sub.add_parser("check", help="check a tools/list JSON file or a stdio server (after --)")
    c.add_argument("file", nargs="?", help="JSON file with a tools/list result")
    c.add_argument("--data-dir", default=check.DATA_DIR)
    c.add_argument("--json", action="store_true", help="print machine-readable output")
    p = sub.add_parser("probe", help="measure the probe corpus against live APIs (needs API keys in env)")
    p.add_argument("--only", help="comma-separated probe ids (control always included)")
    p.add_argument("--data-dir", default=check.DATA_DIR)
    r = sub.add_parser("scan", help="list tools of every server in a targets file and judge both stages")
    r.add_argument("targets")
    r.add_argument("--out", required=True)
    r.add_argument("--timeout", type=float, default=90)
    r.add_argument("--data-dir", default=check.DATA_DIR)
    args = ap.parse_args(argv)

    if args.sub == "scan":
        scan.run_scan(args.targets, args.out, args.data_dir, args.timeout)
        return 0

    if args.sub == "probe":
        only = set(args.only.split(",")) if args.only else None
        results, skipped = runner.run_all(only=only)
        if results:
            runner.save(results, skipped, args.data_dir)
        bad = {r["config"] for r in results} - runner.valid_configs(results)
        for cfg in sorted(bad):
            print("%s: control probe was not accepted; results NOT merged into latest.json" % cfg, file=sys.stderr)
        return 1 if bad else 0

    try:
        if cmd:
            tools = stdio.list_tools(cmd)
        elif args.file:
            with open(args.file, encoding="utf-8") as f:
                tools = check.load_tools(json.load(f))
        else:
            ap.error("give a JSON file or a server command after --")
    except (OSError, ValueError, stdio.StdioError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    results, failed = check.run_check(tools, check.load_table(args.data_dir))
    if args.json:
        print(json.dumps([{"tool": n, "configs": ev} for n, ev in results], indent=1))
    else:
        print(check.render(results))
    return 1 if failed else 0
