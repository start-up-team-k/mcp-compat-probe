import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from mcp_compat_probe import check, cli, runner, scan, stdio
from mcp_compat_probe.probes import BY_ID, PROBES, match_probes

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
DATA = check.DATA_DIR


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def rd(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class Corpus(unittest.TestCase):
    def test_size_and_control(self):
        ids = [p["id"] for p in PROBES]
        self.assertGreaterEqual(len(ids), 30)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("control", ids)

    def test_each_probe_detects_itself_and_serializes(self):
        for p in PROBES:
            t = p["tool"]
            json.dumps(t)
            self.assertTrue(p["match"](t["name"], t["inputSchema"], t["description"]), p["id"])
            if p["id"] != "control":
                self.assertIn(p["id"], match_probes(t))

    def test_control_matches_nothing(self):
        self.assertEqual(match_probes(BY_ID["control"]["tool"]), [])

    def test_name_probes(self):
        self.assertGreater(len(BY_ID["name_too_long"]["tool"]["name"]), 64)
        self.assertGreater(len(BY_ID["long_description"]["tool"]["description"]), 5000)


class IssueDerivedExpectations(unittest.TestCase):
    """E-003 (notion-mcp-server#340) and E-004 (n8n#15553); expectations are NOT measurements."""

    def setUp(self):
        self.table = check.load_table(DATA_ONLY_EXPECTED)

    def test_e003_top_level_combinators(self):
        for probe in ("top_anyof", "top_oneof", "top_allof"):
            ev = check.evaluate(BY_ID[probe]["tool"], self.table)
            for cfg in ("anthropic_messages", "openai_chat"):
                self.assertEqual(ev[cfg]["status"], "FAIL")
                self.assertEqual(ev[cfg]["source"], "issue-derived")
            self.assertEqual(ev["gemini_parameters"]["status"], "UNKNOWN")

    def test_e004_gemini_type_array(self):
        ev = check.evaluate(BY_ID["type_array_null"]["tool"], self.table)
        self.assertEqual(ev["gemini_parameters"]["status"], "FAIL")
        self.assertEqual(ev["anthropic_messages"]["status"], "UNKNOWN")

    def test_expected_file_is_labelled_not_measured(self):
        doc = json.loads(rd(DATA, "expected-from-issues.json"))
        self.assertIn("NOT MEASURED", doc["_comment"])
        for e in doc["expectations"]:
            self.assertIs(e["measured"], False)
            self.assertTrue(e["source_url"].startswith("https://github.com/"))
            self.assertTrue(e["quote"])


DATA_ONLY_EXPECTED = tempfile.mkdtemp()
with open(os.path.join(DATA_ONLY_EXPECTED, "expected-from-issues.json"), "w") as _g:
    _g.write(rd(DATA, "expected-from-issues.json"))


class Check(unittest.TestCase):
    def test_measured_overrides_expected(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "expected-from-issues.json"), "w") as f:
            f.write(rd(DATA, "expected-from-issues.json"))
        rows = [{"probe_id": p, "config": "anthropic_messages", "accepted": True,
                 "measured_at": "2026-01-02T00:00:00Z"} for p in ("control", "top_anyof")]
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump({"results": rows}, f)
        ev = check.evaluate(BY_ID["top_anyof"]["tool"], check.load_table(d))
        self.assertEqual(ev["anthropic_messages"]["status"], "PASS")
        self.assertEqual(ev["anthropic_messages"]["source"], "measured 2026-01-02")
        self.assertEqual(ev["openai_chat"]["status"], "FAIL")  # still issue-derived

    def test_cli_file_fails_with_nonzero_exit(self):
        code, out, _ = run_cli("check", os.path.join(ROOT, "examples", "tools-list.json"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL (issue)", out)
        self.assertIn("issue-derived", out)

    def test_cli_exit_zero_when_nothing_fails(self):
        f = os.path.join(tempfile.mkdtemp(), "t.json")
        with open(f, "w") as fh:
            json.dump({"result": {"tools": [BY_ID["control"]["tool"]]}}, fh)
        code, out, _ = run_cli("check", f)
        self.assertEqual(code, 0)
        self.assertIn("UNKNOWN", out)

    def test_bad_input_exit_two(self):
        f = os.path.join(tempfile.mkdtemp(), "t.json")
        with open(f, "w") as fh:
            fh.write('{"nope": 1}')
        self.assertEqual(run_cli("check", f)[0], 2)

    def test_stdio_server(self):
        server = os.path.join(HERE, "fake_server.py")
        self.assertEqual([t["name"] for t in stdio.list_tools([sys.executable, server])], ["a", "b"])
        code, out, _ = run_cli("check", "--", sys.executable, server)
        self.assertEqual(code, 1)  # tool "a" has a top-level anyOf
        self.assertIn("a ", out)

    def test_stdio_missing_command(self):
        self.assertEqual(run_cli("check", "--", "definitely-not-a-command-xyz")[0], 2)


class Runner(unittest.TestCase):
    KEYS = {"ANTHROPIC_API_KEY": "sk-ant-FAKEKEY123456789", "OPENAI_API_KEY": "sk-FAKEOPENAIKEY987654",
            "GEMINI_API_KEY": "AIzaFAKEGEMINIKEY1234567890abc"}

    def fake_urlopen(self, req, timeout=None):
        # A hostile server that echoes our credentials back in the error body.
        leaked = json.dumps(dict(req.header_items()))
        body = ('{"error":{"message":"bad schema; your key %s was rejected; headers %s"}}' % (
            ",".join(self.KEYS.values()), leaked)).encode()
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    def test_keys_never_reach_output_or_results(self):
        d = tempfile.mkdtemp()
        out, err = io.StringIO(), io.StringIO()
        env = dict(self.KEYS)
        with mock.patch("urllib.request.urlopen", self.fake_urlopen), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["probe", "--only", "top_anyof", "--data-dir", d])
        blob = out.getvalue() + err.getvalue()
        for name in os.listdir(os.path.join(d, "results")):
            blob += rd(d, "results", name)
        blob += rd(d, "latest.json")
        for k in self.KEYS.values():
            self.assertNotIn(k, blob)
        self.assertIn("***", blob)
        self.assertEqual(code, 1)  # control rejected -> run not merged
        self.assertEqual(json.loads(rd(d, "latest.json"))["results"], [])

    def test_network_exception_is_masked(self):
        def boom(req, timeout=None):
            raise OSError("cannot reach %s with %s" % (req.full_url, self.KEYS["GEMINI_API_KEY"]))
        with mock.patch("urllib.request.urlopen", boom):
            r = runner.measure("gemini_parameters", BY_ID["control"], dict(self.KEYS), sleep=lambda s: None)
        self.assertIsNone(r["accepted"])
        self.assertNotIn(self.KEYS["GEMINI_API_KEY"], json.dumps(r))

    def test_no_keys_are_skipped_not_faked(self):
        logs = []
        results, skipped = runner.run_all(env={}, log=logs.append)
        self.assertEqual(results, [])
        self.assertEqual(len(skipped), len(runner.CONFIGS))
        self.assertTrue(all(s["reason"] == "skipped: no key" for s in skipped))

    def test_success_records_fields_and_merges(self):
        def ok(req, timeout=None):
            body = json.dumps({"model": "m-resp", "usage": {"input_tokens": 7, "output_tokens": 1}}).encode()
            return mock.MagicMock(status=200, read=lambda: body,
                                  __enter__=lambda s: s, __exit__=lambda *a: False)
        d = tempfile.mkdtemp()
        with mock.patch("urllib.request.urlopen", ok):
            results, skipped = runner.run_all(env={"ANTHROPIC_API_KEY": "sk-ant-x1234567"}, only={"const"}, log=lambda m: None)
        doc = runner.save(results, skipped, d)
        r = results[0]
        self.assertTrue(r["accepted"])
        self.assertEqual((r["model_returned"], r["http_status"], r["usage"]["input_tokens"]), ("m-resp", 200, 7))
        self.assertEqual(r["transport"], "rest-direct")
        self.assertTrue(r["measured_at"].endswith("Z"))
        latest = json.loads(rd(d, "latest.json"))
        self.assertEqual(len(latest["results"]), 4)  # (control + const) x 2 anthropic configs
        self.assertTrue(os.path.exists(os.path.join(d, "results", doc["date"] + ".json")))

    def test_request_shapes(self):
        t = BY_ID["control"]["tool"]
        _, _, b = runner.build("openai_chat_strict", "m", "k", t)
        self.assertTrue(b["tools"][0]["function"]["strict"])
        for cfg, want in (("openai_responses", None), ("openai_responses_strict_true", True),
                          ("openai_responses_strict_false", False)):
            url, _, b = runner.build(cfg, "m", "k", t)
            self.assertTrue(url.endswith("/v1/responses"))
            self.assertEqual(b["tools"][0].get("strict", "absent"), "absent" if want is None else want)
        _, _, b = runner.build("openai_chat", "m", "k", t)
        self.assertNotIn("strict", b["tools"][0]["function"])
        _, _, b = runner.build("gemini_parameters_json_schema", "m", "k", t)
        self.assertIn("parametersJsonSchema", b["tools"][0]["functionDeclarations"][0])
        url, h, b = runner.build("anthropic_count_tokens", "m", "SECRETKEY99", t)
        self.assertTrue(url.endswith("/count_tokens"))
        self.assertNotIn("SECRETKEY99", url + json.dumps(b))  # key only in headers


class StdioHang(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_timeout_returns_even_if_grandchild_holds_pipe(self):
        with self.assertRaises(stdio.StdioError):
            stdio.list_tools(["sh", "-c", "sleep 60 & sleep 60"], timeout=1)


class Scan(unittest.TestCase):
    def tool(self, schema):
        return {"name": "t", "description": "d", "inputSchema": schema}

    def test_client_stage_dialects(self):
        d07 = scan.client_stage(self.tool({"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}))
        self.assertEqual(d07["dialect"], "draft-07")
        self.assertIn("2020-12-only", d07["risk"])
        new = scan.client_stage(self.tool({"$schema": "https://json-schema.org/draft/2020-12/schema",
                                           "type": "object", "properties": {"a": {"prefixItems": []}}}))
        self.assertEqual(new["dialect"], "2020-12")
        self.assertIsNone(scan.client_stage(self.tool({"type": "object"})))
        mixed = scan.client_stage(self.tool({"$schema": "http://json-schema.org/draft-07/schema#",
                                             "properties": {"a": {"prefixItems": []}}}))
        self.assertIn("prefixItems", mixed["keywords"])

    def test_scan_one_statuses_and_judging(self):
        d = tempfile.mkdtemp()
        table = check.load_table(DATA_ONLY_EXPECTED)
        base = {"name": "a/b", "command": ["x"]}
        needs = scan.scan_one(dict(base, env=["API_KEY"]), d, table)
        self.assertEqual(needs["status"], "needs config")

        def boom(cmd, timeout):
            raise stdio.StdioError("server exited before answering initialize; stderr: Error: API_KEY is not set")
        self.assertEqual(scan.scan_one(base, d, table, lister=boom)["status"], "needs config")

        def crash(cmd, timeout):
            raise stdio.StdioError("timeout waiting for initialize")
        self.assertEqual(scan.scan_one(base, d, table, lister=crash)["status"], "exec failed")
        tools = [self.tool({"type": "object", "anyOf": [{}]})]
        ok = scan.scan_one(base, d, table, lister=lambda c, timeout: tools)
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["api_stage"]["configs"]["anthropic_messages"]["FAIL"], 1)
        self.assertEqual(ok["api_stage"]["configs"]["anthropic_messages"]["sources"], ["issue-derived"])
        self.assertTrue(os.path.exists(os.path.join(d, ok["tools_file"])))

    def test_scan_cli_with_fake_server(self):
        d = tempfile.mkdtemp()
        tf = os.path.join(d, "t.json")
        with open(tf, "w") as f:
            json.dump({"targets": [{"name": "fake/one", "command": [sys.executable, os.path.join(HERE, "fake_server.py")]}]}, f)
        code, _, _ = run_cli("scan", tf, "--out", os.path.join(d, "2026-01-01"), "--timeout", "20")
        self.assertEqual(code, 0)
        doc = json.loads(rd(d, "2026-01-01", "results.json"))
        self.assertEqual(doc["servers"][0]["n_tools"], 2)


if __name__ == "__main__":
    unittest.main()
