"""Checks tapir.py against a fake Archicad JSON port - no Archicad needed.  python tests/test_tapir.py"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "archicad-tapir", "scripts"))
os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"        # a proxy that would swallow every call that used it
for name in ("NO_PROXY", "no_proxy"):
    os.environ.pop(name, None)
import tapir  # noqa: E402

REPLIES = {
    "API.IsAlive": {"succeeded": True, "result": {"isAlive": True}},
    "API.Broken": {"succeeded": False, "error": {"code": 4002, "message": "bad parameter"}},
    "GetProjectInfo": {"succeeded": True, "result": {"addOnCommandResponse": {"projectName": "演示"}}},
    "QuitArchicad": {"succeeded": True, "result": {"addOnCommandResponse": {"success": False,
                                                                             "error": {"code": 1, "message": "no"}}}},
    "Run": {"succeeded": True, "result": {"addOnCommandResponse": {"said": "done"}}},
}
seen = []


class FakeArchicad(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append(body)
        name = body["parameters"]["addOnCommandId"]["commandName"] if body["command"] == "API.ExecuteAddOnCommand" \
            else body["command"]
        data = json.dumps(REPLIES[name], ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), FakeArchicad)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]

assert tapir.call("API.IsAlive", port=port) == {"isAlive": True}
assert seen[-1] == {"command": "API.IsAlive"}

assert tapir.call("GetProjectInfo", port=port) == {"projectName": "演示"}
assert seen[-1]["parameters"] == {"addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": "GetProjectInfo"},
                                  "addOnCommandParameters": {}}

assert tapir.call("MyAddOn/Run", {"level": 2}, port=port) == {"said": "done"}
assert seen[-1]["parameters"]["addOnCommandId"] == {"commandNamespace": "MyAddOn", "commandName": "Run"}
assert seen[-1]["parameters"]["addOnCommandParameters"] == {"level": 2}

for failing in ("API.Broken", "QuitArchicad"):          # a failed command, and Tapir's failed ExecutionResult
    try:
        tapir.call(failing, port=port)
        raise AssertionError(failing + " should raise")
    except RuntimeError as e:
        assert "message" in str(e), e

assert tapir.parameters([]) is None
assert tapir.parameters(["elementType=Wall", "storyIndex=2", "hidden=true", "ids=[1,2]"]) == \
    {"elementType": "Wall", "storyIndex": 2, "hidden": True, "ids": [1, 2]}
assert tapir.parameters(['{"elements": []}']) == {"elements": []}
with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8-sig", delete=False) as f:
    f.write('{"name": "图层"}')
assert tapir.parameters(["@" + f.name]) == {"name": "图层"}
os.remove(f.name)

assert tapir.outline({"elements": [1, 2, 3], "nested": {"ids": []}, "ok": True}) == \
    {"elements": "3 items", "nested": {"ids": "0 items"}, "ok": True}
assert tapir.numbers("1.7.0") < tapir.numbers("1.7.1") < tapir.numbers("1.10.0")

server.shutdown()
print("ok")
