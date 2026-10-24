"""Checks tapir.py against a fake Archicad JSON port - no Archicad needed.  python tests/test_tapir.py"""
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
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
    "Slow": {"succeeded": True, "result": {"addOnCommandResponse": {"said": "late"}}},
    "GetElementsByType": {"succeeded": True, "result": {"addOnCommandResponse": {                 # a bare ErrorItem
        "error": {"code": -2130313112, "message": "Invalid elementType 'Walls'."}}}},
    "GetDetailsOfElements": {"succeeded": True, "result": {"addOnCommandResponse": {"detailsOfElements": [
        {"error": {"code": -2130313115, "message": "Failed to get the details of element"}}]}}},
}
seen = []


class FakeArchicad(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append(body)
        name = body["parameters"]["addOnCommandId"]["commandName"] if body["command"] == "API.ExecuteAddOnCommand" \
            else body["command"]
        if name == "Slow":
            time.sleep(1)
        data = json.dumps(REPLIES[name], ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), FakeArchicad)
server.handle_error = lambda *args: None               # the client that timed out has gone
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

for failing in ("API.Broken", "QuitArchicad", "GetElementsByType"):   # a failed command, Tapir's failed
    try:                                                              # ExecutionResult, and its bare ErrorItem
        tapir.call(failing, port=port)
        raise AssertionError(failing + " should raise")
    except RuntimeError as e:
        assert "message" in str(e), e

# one element's error inside a list is data: it is returned in its place, for the caller to check
assert tapir.call("GetDetailsOfElements", {"elements": []}, port=port)["detailsOfElements"][0]["error"]["code"] == \
    -2130313115


def exits(function, *args):
    """The message function(*args) stops with - a SystemExit, not a traceback."""
    try:
        function(*args)
    except SystemExit as e:
        return str(e.code)
    raise AssertionError("%s%r should exit" % (function.__name__, args))


assert "PowerShell" in exits(tapir.parameters, ["{elementType: Wall}"])     # inline JSON with its quotes stripped
assert "needs a value" in exits(tapir.main, ["call", "Run", "--out"])
assert "needs a number" in exits(tapir.main, ["call", "Run", "--port", "x"])
assert tapir.timed_out(socket.timeout("timed out"))
assert tapir.timed_out(urllib.error.URLError(socket.timeout("timed out")))
assert not tapir.timed_out(ConnectionRefusedError(10061, "refused"))

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

# an Archicad that runs without a port (still starting, or a dialog waits): doctor, quit and open must not take it
# for "no Archicad", and open must not start a second one
saved = {name: getattr(tapir, name) for name in ("archicads", "archicad_windows", "listening", "latest_tapir",
                                                 "subprocess", "os")}
started = []
tapir.archicads, tapir.listening = (lambda: []), (lambda port: False)
tapir.archicad_windows = lambda: {123: ["Archicad Project Recovery"]}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
tapir.subprocess = types.SimpleNamespace(run=subprocess.run, Popen=lambda *args, **kwargs: started.append(args))
tapir.os = types.SimpleNamespace(path=os.path, startfile=lambda *args: started.append(args))   # only tapir's view
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(19723) == 1
        assert tapir.quit_archicad(19723, 1) == 1
        assert tapir.open_project(os.path.abspath(__file__), None, 1) == 1
    assert said.getvalue().count("answers on no port (pid 123 shows: Archicad Project Recovery)") == 3, said.getvalue()
    assert "no Archicad" not in said.getvalue() and not started, said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# a call that times out: the command goes on in Archicad, so the message must say not to send it again
assert "do not send it again" in exits(tapir.main, ["call", "Slow", "--port", str(port), "--timeout", "0.3"])

server.shutdown()
print("ok")
