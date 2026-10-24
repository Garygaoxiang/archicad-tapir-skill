"""Checks tapir.py against a fake Archicad JSON port - no Archicad needed.  python tests/test_tapir.py"""
import contextlib
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "archicad-tapir", "scripts")
sys.path.insert(0, SCRIPTS)
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
tapir.PORT = port                                      # a command that misses its --port reaches the fake, not Archicad
launched = []                                          # and nothing tested here may start a program
os.startfile = lambda *args: launched.append(args)


def no_launch(command, *args, **kwargs):
    """subprocess.run, except that open's launcher is only recorded."""
    if tapir.LAUNCH in command:
        launched.append(command)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return subprocess.run(command, *args, **kwargs)


tapir.subprocess = types.SimpleNamespace(run=no_launch, Popen=lambda *args, **kwargs: launched.append(args))

assert tapir.call("API.IsAlive", port=port) == {"isAlive": True}
assert seen[-1] == {"command": "API.IsAlive"}

assert tapir.call("GetProjectInfo", port=port) == {"projectName": "演示"}
assert seen[-1]["parameters"] == {"addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": "GetProjectInfo"},
                                  "addOnCommandParameters": {}}

assert tapir.call("MyAddOn/Run", {"level": 2}, port=port) == {"said": "done"}
assert seen[-1]["parameters"]["addOnCommandId"] == {"commandNamespace": "MyAddOn", "commandName": "Run"}
assert seen[-1]["parameters"]["addOnCommandParameters"] == {"level": 2}

# parameters that are not one JSON object crash Archicad: refused before anything is sent
for wrong in (["Wall"], "Wall", []):
    for command in ("GetElementsByType", "API.GetElementsByType"):
        count = len(seen)
        try:
            tapir.call(command, wrong, port=port)
            raise AssertionError("%s %r should raise" % (command, wrong))
        except ValueError as e:
            assert "elements" in str(e) and len(seen) == count, (e, seen[count:])

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


class Out(io.StringIO):
    """A stdout to capture main()'s output in: main() reconfigures it."""
    def reconfigure(self, **kwargs):
        pass


assert "PowerShell" in exits(tapir.parameters, ["{elementType: Wall}"])     # inline JSON with its quotes stripped
assert "cmd.exe" in exits(tapir.parameters, ["'{level:", "2}'"])             # and as cmd.exe splits it
assert "not both" in exits(tapir.parameters, ["@x.json", "a=1"])
assert "needs a value" in exits(tapir.main, ["call", "Run", "--out"])
assert "needs a number" in exits(tapir.main, ["call", "Run", "--port", "x"])
assert tapir.timed_out(socket.timeout("timed out"))
assert tapir.timed_out(urllib.error.URLError(socket.timeout("timed out")))
assert not tapir.timed_out(ConnectionRefusedError(10061, "refused"))

assert tapir.parameters([]) is None
assert tapir.parameters(["elementType=Wall", "storyIndex=2", "hidden=true", "ids=[1,2]"]) == \
    {"elementType": "Wall", "storyIndex": 2, "hidden": True, "ids": [1, 2]}
# a key=value list or object whose quotes PowerShell 5.1 / cmd.exe stripped is refused, not sent as a string
assert "@file.json\" for a list or object" in exits(tapir.parameters, ["filters=[OnActualFloor]"])
assert "@file.json" in exits(tapir.parameters, ["elementType=Slab", "x={a: b}"])
assert tapir.parameters(['filters=["OnActualFloor"]', "name=Plan [draft]"]) == \
    {"filters": ["OnActualFloor"], "name": "Plan [draft]"}
assert tapir.parameters(['{"elements": []}']) == {"elements": []}
with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8-sig", delete=False) as f:
    f.write('{"name": "图层"}')
assert tapir.parameters(["@" + f.name]) == {"name": "图层"}
os.remove(f.name)

# a @file that cannot be read is not a connection problem, and nothing is sent
count = len(seen)
missing = exits(tapir.main, ["call", "Run", "@" + f.name, "--port", str(port)])
assert "cannot read" in missing and "no answer" not in missing and len(seen) == count, missing

# a bare list on the command line exits with the message and sends nothing
assert "one JSON object" in exits(tapir.main, ["call", "Run", '[{"elementId": {"guid": "x"}}]', "--port", str(port)])
assert len(seen) == count

# --help anywhere only prints the usage, and an option written --port=N is refused: quit must not quit an Archicad
for args in (["quit", "--help"], ["quit", "-h", "--port", str(port)], ["call", "Run", "--help"], ["open", "-h"]):
    with contextlib.redirect_stdout(Out()) as said:
        assert tapir.main(args) == 0
    assert "tapir.py doctor" in said.getvalue() and len(seen) == count, args
for args in (["quit", "--port=%d" % port], ["call", "Run", "--port=%d" % port, "--timeout=5"]):
    assert "unknown option --port=" in exits(tapir.main, args) and len(seen) == count, args

# --out that cannot be written: nothing is sent; a failed call keeps the previous result
folder = tempfile.mkdtemp()
assert "nothing was sent" in exits(tapir.main, ["call", "Run", "--port", str(port), "--out",
                                                os.path.join(folder, "no such folder", "out.json")])
assert len(seen) == count
out = os.path.join(folder, "out.json")
with open(out, "w", encoding="utf-8") as f:
    f.write("previous")
assert "bad parameter" in exits(tapir.main, ["call", "API.Broken", "--port", str(port), "--out", out])
with open(out, encoding="utf-8") as f:
    assert f.read() == "previous"
with contextlib.redirect_stdout(Out()) as said:
    assert tapir.main(["call", "Run", "--port", str(port), "--out", out]) == 0
with open(out, encoding="utf-8") as f:
    assert json.load(f) == {"said": "done"}
assert "wrote" in said.getvalue()

assert tapir.outline({"elements": [1, 2, 3], "nested": {"ids": []}, "ok": True}) == \
    {"elements": "3 items", "nested": {"ids": "0 items"}, "ok": True}
assert tapir.outline({"detailsOfElements": [{}, {}, {"type": "Wall"}]}) == {"detailsOfElements": "3 items (2 empty)"}
assert tapir.outline({"detailsOfElements": [{"error": {"code": 1}}, {}, {"type": "Wall"}]}) == \
    {"detailsOfElements": "3 items (1 empty, 1 failed)"}
assert tapir.outline({"executionResults": [{"success": False, "error": {}}, {"success": True}]}) == \
    {"executionResults": "2 items (1 failed)"}
# one property (or classification) of an element failed, one level down; a details text named error is data
assert tapir.outline({"propertyValuesForElements": [{"propertyValues": [{"error": {}}]}, {"propertyValues": []}]}) == \
    {"propertyValuesForElements": "2 items (1 failed)"}
unsupported = {"type": "Dimension", "details": {"error": "Not yet supported element type"}}
assert tapir.outline({"detailsOfElements": [unsupported]}) == {"detailsOfElements": "1 items"}
assert tapir.numbers("1.7.0") < tapir.numbers("1.7.1") < tapir.numbers("1.10.0")

# an Archicad that runs without a port (still starting, or a dialog waits): doctor, quit and open must not take it
# for "no Archicad", and open must not start a second one
saved = {name: getattr(tapir, name) for name in ("archicads", "archicad_windows", "listening", "latest_tapir",
                                                 "subprocess", "os", "bug_reporter")}
started = []
tapir.archicads, tapir.listening = (lambda: []), (lambda port: False)
tapir.archicad_windows = lambda: {123: ["Archicad Project Recovery"]}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
tapir.subprocess = types.SimpleNamespace(run=no_launch, Popen=lambda *args, **kwargs: started.append(args))
tapir.os = types.SimpleNamespace(path=os.path, startfile=lambda *args: started.append(args))   # only tapir's view
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(19723) == 1
        assert tapir.quit_archicad(19723, 1) == 1
        assert tapir.open_project(os.path.abspath(__file__), None, 1) == 1
    assert said.getvalue().count("answers on no port (pid 123 shows: Archicad Project Recovery)") == 3, said.getvalue()
    assert "no Archicad" not in said.getvalue() and not started, said.getvalue()

    # no Archicad left but its Bug Reporter: doctor says it crashed
    tapir.archicad_windows, tapir.bug_reporter = (lambda: {}), (lambda: True)
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(19723) == 1
    assert "Archicad crashed" in said.getvalue() and "no Archicad listens" not in said.getvalue(), said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# 'This project is in use' (after a crash or a forced end): Archicad already listens but answers 4001 'no open
# project' - doctor, quit and open must name the dialog, not blame another program, unsaved changes or a refusal
asked = []


class NoProject(FakeArchicad):
    def do_POST(self):
        asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        data = b'{"succeeded": false, "error": {"code": 4001, "message": "Invalid program status (no open project)"}}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


in_use = HTTPServer(("127.0.0.1", 0), NoProject)
threading.Thread(target=in_use.serve_forever, daemon=True).start()
busy = in_use.server_address[1]
saved = {name: getattr(tapir, name) for name in ("PORTS", "archicad_windows", "latest_tapir", "listening_pid")}
tapir.PORTS, tapir.listening_pid = range(busy, busy + 1), (lambda port: None)
tapir.archicad_windows = lambda: {123: ["Information"]}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(busy) == 1
        assert tapir.quit_archicad(busy, 1) == 1
        assert tapir.open_project(os.path.abspath(__file__), None, 1) == 1
    assert said.getvalue().count("no project open (pid 123 shows: Information)") == 3, said.getvalue()
    assert "unsaved" not in said.getvalue() and "refused" not in said.getvalue(), said.getvalue()
    assert "QuitArchicad" not in json.dumps(asked), asked
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)
    in_use.shutdown()

# 4001 for another reason (a modal dialog, the user mid-input): it is Archicad with Tapir, and the project it has open
# may be this one - doctor, quit and open must say it refuses for now, not 'not Archicad', 'install' or 'switch'
asked, product = [], {"answers": False, "reason": "ongoing user input"}


class MidInput(FakeArchicad):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        asked.append(body)
        reply = {"succeeded": False, "error": {"code": 4001, "message": "Invalid program status (%s)" % product["reason"]}}
        if body["command"] == "API.GetProductInfo" and product["answers"]:
            reply = {"succeeded": True, "result": {"version": 28, "languageCode": "INT", "buildNumber": 1}}
        data = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


mid = HTTPServer(("127.0.0.1", 0), MidInput)
threading.Thread(target=mid.serve_forever, daemon=True).start()
busy = mid.server_address[1]
saved = {name: getattr(tapir, name) for name in ("PORTS", "archicad_windows", "latest_tapir", "listening_pid")}
tapir.PORTS, tapir.listening_pid = range(busy, busy + 1), (lambda port: None)
tapir.archicad_windows = lambda: {123: ["Demo - Archicad 28"]}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(busy) == 1
        product["answers"] = True                  # the user starts typing between doctor's first two calls
        assert tapir.doctor(busy) == 1
        product["answers"] = False
        assert tapir.quit_archicad(busy, 1) == 1
        assert tapir.open_project(os.path.abspath(__file__), None, 1) == 1
        product.update(answers=True, reason="no open project")          # a built-in command that needs no project
        assert tapir.doctor(busy) == 1
    assert said.getvalue().count("refuses commands now") == 4, said.getvalue()
    assert "build 1 runs with no project open (pid 123" in said.getvalue(), said.getvalue()
    for wrong in ("does not answer like Archicad", "Tapir not loaded", "Switching"):
        assert wrong not in said.getvalue(), said.getvalue()
    assert "QuitArchicad" not in json.dumps(asked), asked
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)
    mid.shutdown()

# open: the Archicad it started ends before the project is open (a crash, or the user closed it) - say so at once, not
# after --wait; one poll with no Archicad between two processes is a hand-over, not an end. A fake clock: no waiting.
clock = [0.0]
saved = {name: getattr(tapir, name) for name in ("archicads", "archicad_windows", "bug_reporter", "time", "subprocess")}
tapir.archicads = lambda: []
tapir.time = types.SimpleNamespace(time=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
tapir.subprocess = types.SimpleNamespace(run=lambda *args, **kwargs: types.SimpleNamespace(returncode=0),
                                         Popen=lambda *args, **kwargs: None)                 # 'starts' Archicad
try:
    for polls, reporter, wanted, unwanted in (
            ([{5: ["Archicad Project Recovery"]}, {}, {6: ["Untitled - Archicad 28"]}, {}, {}], False,
             ["pid 5) ended", "pid 6) ended", "ended 25 s after it started", "crashed or was closed"], "did not answer"),
            ([{5: ["Archicad 28"]}, {}, {}], True, ["ended 15 s after", "Bug Reporter"], "did not answer"),
            ([], False, ["did not answer for the project in 30 s"], "ended")):   # tasklist blocked: none is ever seen
        polls, clock[0] = [{}] + polls, 0.0        # the first: open's check that none runs yet
        tapir.archicad_windows = lambda: polls.pop(0) if polls else {}
        tapir.bug_reporter = lambda: reporter
        with contextlib.redirect_stdout(io.StringIO()) as said:
            assert tapir.open_project(os.path.abspath(__file__), None, 30) == 1
        assert all(w in said.getvalue() for w in wanted) and unwanted not in said.getvalue(), said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# Archicad busy (no answer in time): quit must not send a quit that would run later, open must not call it "another
# project" and suggest quitting it
sent = []


def no_answer(command, params=None, port=None, timeout=None):
    sent.append(command)
    raise urllib.error.URLError(socket.timeout("timed out"))


saved = {name: getattr(tapir, name) for name in ("call", "PORTS", "subprocess", "os")}
started = []
tapir.call, tapir.PORTS = no_answer, range(port, port + 1)                      # the fake port listens
tapir.subprocess = types.SimpleNamespace(run=no_launch, Popen=lambda *args, **kwargs: started.append(args))
tapir.os = types.SimpleNamespace(path=os.path, startfile=lambda *args: started.append(args))
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.quit_archicad(port, 1) == 1
        assert sent == ["GetArchicadLocation"], sent
        assert tapir.open_project(os.path.abspath(__file__), None, 1) == 1
    assert "Not quitting" in said.getvalue() and "does not answer in 5 s" in said.getvalue(), said.getvalue()
    assert "unsaved" not in said.getvalue() and not started, said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# a sandbox that forbids connecting to 127.0.0.1 makes every port look closed: say so, not "no Archicad"
create_connection = socket.create_connection
socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError(13, "forbidden"))
saved = {name: getattr(tapir, name) for name in ("latest_tapir", "archicad_windows")}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
tapir.archicad_windows = lambda: {}
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(port) == 1
    assert "not allowed" in said.getvalue() and "no Archicad listens" not in said.getvalue(), said.getvalue()
    for args in (["quit"], ["open", os.path.abspath(__file__)], ["call", "Run"]):
        assert "not allowed" in exits(tapir.main, args + ["--port", str(port)]), args
finally:
    socket.create_connection = create_connection
    for name, value in saved.items():
        setattr(tapir, name, value)

# tasklist or PowerShell blocked by policy: only the ports are known, no traceback
saved = tapir.subprocess
tapir.subprocess = types.SimpleNamespace(run=lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError(5, "blocked")))
try:
    assert tapir.tasks("Archicad.exe") == [] and tapir.listening_pid(port) is None
finally:
    tapir.subprocess = saved

# Archicad drops the connection mid-command (it crashed): say so, and not to send the command again
crash = socket.socket()
crash.bind(("127.0.0.1", 0))
crash.listen(1)


def drop():
    connection, _ = crash.accept()
    connection.recv(65536)
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # a reset, as on a crash
    connection.close()


threading.Thread(target=drop, daemon=True).start()
dropped = exits(tapir.main, ["call", "Run", "--port", str(crash.getsockname()[1])])
assert "crashed" in dropped and "Do not send the command again" in dropped, dropped
crash.close()

if tapir.WINDOWS:
    # a dialog is listed with its text, read without sending it a message (the child below never pumps any)
    child = subprocess.Popen([sys.executable, "-c", r'''
import ctypes, time
from ctypes import wintypes
u = ctypes.WinDLL("user32")
u.CreateWindowExW.restype = wintypes.HWND
u.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                              wintypes.HINSTANCE, wintypes.LPVOID)
box = u.CreateWindowExW(0x08000080, "Static", "Information", 0x90000000, -4000, -4000, 300, 100, None, None, None, None)
u.CreateWindowExW(0, "Static", "", 0x50000000, 0, 0, 300, 20, box, None, None, None)
u.CreateWindowExW(0, "Static", '"demo.pln"\r\nThis project is in use.', 0x50000000, 0, 20, 300, 50, box, None, None, None)
print("up", flush=True)
time.sleep(60)
'''], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "up"
        assert tapir.window_titles(child.pid) == ['Information: "demo.pln" This project is in use.'], \
            tapir.window_titles(child.pid)
    finally:
        child.kill()
        child.wait()

    # dbwin starts a fresh log, so that the pid on its first line is the one that runs
    log = os.path.join(folder, "dbwin.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("listening, pid 1\nan old line\n")
    child = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "tapir.py"), "dbwin", "--out", log],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    try:
        first = child.stdout.readline()
        if first.startswith("listening"):          # else another debug listener runs on this machine: not testable
            with open(log, encoding="utf-8") as f:
                assert f.read() == "listening, pid %d\n" % child.pid
    finally:
        child.kill()
        child.wait()

# Archicad listens but does not answer (opening a project, or a dialog with its port already open): doctor and quit name
# what it shows
saved = {name: getattr(tapir, name) for name in ("call", "archicads", "archicad_windows", "latest_tapir", "listening")}
tapir.call, tapir.archicads, tapir.listening = no_answer, (lambda: [port]), (lambda p: True)
tapir.archicad_windows = lambda: {7: ['Information: "x.pln" This project is in use by me.']}
tapir.latest_tapir = lambda: (_ for _ in ()).throw(OSError("offline"))
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(port) == 1
        assert tapir.quit_archicad(port, 1) == 1
    assert said.getvalue().count("This project is in use by me") == 2, said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# an Archicad without Tapir (error 4010, a first install) locks nothing of it: an install denied there retries
# elevated, not 'quit it'; quit, which has no QuitArchicad to send, and doctor say that the user restarts it. An
# Archicad with Tapir loaded still locks it, also while it refuses commands (4001).
tapir_answers, elevated = [4010], []


def tapir_call(command, params=None, port=None, timeout=None):
    if command == "API.GetProductInfo":
        return {"version": 28, "languageCode": "INT", "buildNumber": 1}
    if not tapir_answers[0]:
        return {"version": "1.7.1"}
    raise RuntimeError('%s: {"code": %d, "message": "..."}' % (command, tapir_answers[0]))


saved = {name: getattr(tapir, name) for name in ("call", "archicads", "archicad_windows", "latest_tapir", "from_repo",
                                                 "subprocess", "as_admin", "WINDOWS", "listening", "listening_pid")}
tapir.call, tapir.archicads, tapir.archicad_windows = tapir_call, (lambda: [port]), (lambda: {})
tapir.latest_tapir, tapir.from_repo = (lambda: "1.7.2"), (lambda tag, path: "tapir_installer.py")
tapir.subprocess = types.SimpleNamespace(run=lambda *args, **kwargs: types.SimpleNamespace(
    returncode=1, stdout="", stderr="ERROR: Permission denied. Close Archicad if it is running, or run the installer"
                                    " as administrator."))
tapir.as_admin, tapir.WINDOWS = (lambda command: elevated.append(command) or 0), True
tapir.listening, tapir.listening_pid = (lambda p: True), (lambda p: None)
try:
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.install(["--versions", "28"]) == 0 and len(elevated) == 1
        assert tapir.quit_archicad(port, 1) == 1
    assert "locks" not in said.getvalue() and "user to save and quit" in said.getvalue(), said.getvalue()
    assert "refused" not in said.getvalue(), said.getvalue()
    tapir.latest_tapir, tapir.WINDOWS = (lambda: (_ for _ in ()).throw(OSError("offline"))), saved["WINDOWS"]
    with contextlib.redirect_stdout(io.StringIO()) as said:
        assert tapir.doctor(port) == 1
    assert "Tapir not loaded" in said.getvalue() and "quit needs Tapir loaded" in said.getvalue(), said.getvalue()
    tapir.latest_tapir, tapir.WINDOWS = (lambda: "1.7.2"), True
    for tapir_answers[0] in (None, 4001):
        with contextlib.redirect_stdout(io.StringIO()) as said:
            assert tapir.install(["--versions", "28"]) == 1 and len(elevated) == 1
        assert "locks the add-ons" in said.getvalue(), said.getvalue()
finally:
    for name, value in saved.items():
        setattr(tapir, name, value)

# a call that times out: the command goes on in Archicad, so the message must say not to send it again
assert "do not send it again" in exits(tapir.main, ["call", "Slow", "--port", str(port), "--timeout", "0.3"])

if tapir.WINDOWS:
    # open starts Archicad through a process that exits at once, so that an agent stopping open (which ends its whole
    # process tree) does not end Archicad; a program that cannot start is said, not waited for
    saved = {name: getattr(tapir, name) for name in ("archicads", "archicad_windows", "project_port", "subprocess")}
    tapir.archicads, tapir.archicad_windows, tapir.project_port = (lambda: []), (lambda: {}), (lambda *args: None)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            assert tapir.open_project(os.path.abspath(__file__), r"C:\AC\Archicad.exe", 0) == 1
        assert launched[-1][-2:] == [r"C:\AC\Archicad.exe", os.path.abspath(__file__)], launched
        assert launched.pop()[:4] == [sys.executable, "-I", "-c", tapir.LAUNCH], launched
        tapir.subprocess = types.SimpleNamespace(run=subprocess.run, Popen=None)   # the launcher runs, for real
        missing = exits(tapir.open_project, os.path.abspath(__file__), os.path.join(folder, "no.exe"), 0)
        assert "cannot start" in missing and "FileNotFoundError" in missing, missing
    finally:
        for name, value in saved.items():
            setattr(tapir, name, value)

server.shutdown()
shutil.rmtree(folder, ignore_errors=True)
assert not launched, launched
print("ok")
