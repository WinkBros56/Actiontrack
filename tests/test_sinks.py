import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import Fixture, make_people_with_emails
from actiontrack.drafts import Draft
from actiontrack.sinks import JsonSink, McpSink, SinkError, assert_draft_only

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


def draft() -> Draft:
    return Draft("Sam Okafor", ["sam@example.test"], "Subject", "Body text", True, [1, 2])


class SafetyGuard(unittest.TestCase):
    def test_send_tools_are_refused(self):  # AC26
        for name in ("send_message", "gmail_send", "SendDraft", "forward_and_send", ""):
            with self.assertRaises(SinkError, msg=name):
                assert_draft_only(name)
        assert_draft_only("create_draft")

    def test_mcp_sink_refuses_send_tool_before_starting_server(self):
        with self.assertRaises(SinkError):
            McpSink(sys.executable, [FAKE], tool="send_message")

    def test_no_send_path_in_source(self):
        src = Path(__file__).parent.parent / "src" / "actiontrack"
        for f in src.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            for banned in ("send_message", "users().messages().send", "smtplib", "sendmail"):
                self.assertNotIn(banned, text, f"{f.name} mentions {banned}")


class JsonSinkTests(unittest.TestCase):
    def test_payload_matches_create_draft_arguments(self):
        s = JsonSink()
        s.create(draft())
        p = s.payload()["drafts"][0]
        self.assertEqual(p["create_draft"], {"to": ["sam@example.test"], "subject": "Subject", "body": "Body text",
                                             "includeSignature": True})
        self.assertEqual(p["item_ids"], [1, 2])


class McpSinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.log"
        os.environ["FAKE_MCP_LOG"] = str(self.log)
        os.environ.pop("FAKE_MCP_FAIL", None)

    def tearDown(self):
        os.environ.pop("FAKE_MCP_LOG", None)
        os.environ.pop("FAKE_MCP_FAIL", None)
        self.tmp.cleanup()

    def test_creates_draft_via_tool_call(self):
        sink = McpSink(sys.executable, [FAKE], tool="create_draft", timeout=15)
        try:
            ref = sink.create(draft())
            self.assertEqual(ref, "draft-1")
        finally:
            sink.close()
        call = json.loads(self.log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(call["name"], "create_draft")
        self.assertEqual(call["arguments"], {"to": ["sam@example.test"], "subject": "Subject", "body": "Body text",
                                             "includeSignature": True})

    def test_argument_mapping_renames_and_omits_fields(self):
        sink = McpSink(sys.executable, [FAKE], tool="create_draft",
                       arguments={"to": "recipients", "subject": "title", "body": "text", "includeSignature": None},
                       timeout=15)
        try:
            sink.create(draft())
        finally:
            sink.close()
        call = json.loads(self.log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(call["arguments"]), {"recipients", "title", "text"})

    def test_tool_error_is_reported(self):
        os.environ["FAKE_MCP_FAIL"] = "1"
        sink = McpSink(sys.executable, [FAKE], tool="create_draft", timeout=15)
        try:
            with self.assertRaises(SinkError):
                sink.create(draft())
        finally:
            sink.close()

    def test_missing_server_binary(self):
        with self.assertRaises(SinkError):
            McpSink("definitely-not-a-real-binary-xyz", [])

    def test_cli_followup_with_mcp_sink(self):
        cfg = Path(self.tmp.name) / "actiontrack.config.json"
        cfg.write_text(json.dumps({"gmail": {"command": sys.executable, "args": [FAKE], "tool": "create_draft"}}),
                       encoding="utf-8")
        fx = Fixture(make_people_with_emails(Path(self.tmp.name)))
        try:
            code, out, err = fx.cli("followup", "Sam Okafor", "--sink", "mcp", "--config", str(cfg))
            self.assertEqual(code, 0, err)
            self.assertIn("draft-1", err)
            row = fx.conn.execute("SELECT sink, draft_ref FROM followups").fetchone()
            self.assertEqual((row["sink"], row["draft_ref"]), ("mcp", "draft-1"))
            call = json.loads(self.log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(call["arguments"]["to"], ["sam.okafor@example.test"])
        finally:
            fx.close()

    def test_cli_rejects_config_with_send_tool(self):
        cfg = Path(self.tmp.name) / "bad.json"
        cfg.write_text(json.dumps({"gmail": {"command": sys.executable, "args": [FAKE], "tool": "send_message"}}),
                       encoding="utf-8")
        fx = Fixture(make_people_with_emails(Path(self.tmp.name)))
        try:
            code, _, err = fx.cli("followup", "Sam Okafor", "--sink", "mcp", "--config", str(cfg))
            self.assertEqual(code, 1)
            self.assertIn("never a send tool", err)
            self.assertFalse(self.log.exists())
        finally:
            fx.close()


if __name__ == "__main__":
    unittest.main()
