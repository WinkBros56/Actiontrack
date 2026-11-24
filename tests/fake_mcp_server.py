"""A tiny fake MCP stdio server used to test McpSink. Records tool calls to $FAKE_MCP_LOG."""

import json
import os
import sys

LOG = os.environ.get("FAKE_MCP_LOG")
n = 0
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if "id" not in msg:  # notification
        continue
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-gmail", "version": "0"}}
    elif method == "tools/call":
        n += 1
        if LOG:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg["params"]) + "\n")
        if os.environ.get("FAKE_MCP_FAIL"):
            result = {"isError": True, "content": [{"type": "text", "text": "quota exceeded"}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps({"id": f"draft-{n}"})}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
