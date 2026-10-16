#!/usr/bin/env python3
"""Drive a running Archicad through the Tapir add-on's JSON API. Python 3.8+, standard library only.

  tapir.py doctor                       check Python, Archicad and Tapir; every [!!] line says what to do
  tapir.py install [installer options]  install or update Tapir with its official installer, latest release
                                        (--versions 28   --dryRun   --uninstall   --addOnsFolder DIR)
  tapir.py help [Command|Definition]    the commands of the Tapir version Archicad runs, or one command's schema
  tapir.py call Command [params]        run a Tapir command; API.Name runs a built-in one, Namespace/Name an add-on's
  tapir.py quit                         quit Archicad (it asks about unsaved changes) and wait until it is gone
  tapir.py open PROJECT [--exe PATH]    start Archicad on a project and wait until it answers for it
  tapir.py dbwin                        Windows: print OutputDebugString lines (C++ add-on logs) until stopped

params   inline JSON, key=value pairs (each value read as JSON when it parses), @file.json, or - for stdin
options  --port N (default 19723 or $ARCHICAD_PORT)   --timeout SECONDS (call; default 600)
         --out FILE (call, dbwin: write UTF-8 there)   --wait SECONDS (quit 180, open 900)

From Python:  sys.path.insert(0, "<this folder>"); from tapir import call
              walls = call("GetElementsByType", {"elementType": "Wall"})["elements"]
"""
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = "ENZYME-APD/tapir-archicad-automation"
INSTALLER = "archicad-addon/Installer/tapir_installer.py"
PORTS = range(19723, 19745)                    # each Archicad takes the first free one
PORT = int(os.environ.get("ARCHICAD_PORT", "19723"))
WINDOWS = sys.platform == "win32"


def call(command, params=None, port=PORT, timeout=600):
    """Runs one command and returns its result; raises RuntimeError carrying Archicad's error.
    "API.X" is a built-in command, "Namespace/X" an add-on's own, a bare name a Tapir command."""
    if command.startswith("API."):
        body = {"command": command}
        if params is not None:
            body["parameters"] = params
    else:
        namespace, _, name = command.rpartition("/")
        body = {"command": "API.ExecuteAddOnCommand", "parameters": {
            "addOnCommandId": {"commandNamespace": namespace or "TapirCommand", "commandName": name},
            "addOnCommandParameters": params or {}}}
    request = urllib.request.Request("http://127.0.0.1:%d" % port, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.loads(response.read().decode("utf-8"))
    if not reply.get("succeeded"):
        raise RuntimeError("%s: %s" % (command, json.dumps(reply.get("error"), ensure_ascii=False)))
    result = reply.get("result") or {}
    result = result.get("addOnCommandResponse", result)
    if isinstance(result, dict) and result.get("success") is False:        # Tapir's failed ExecutionResult
        raise RuntimeError("%s: %s" % (command, json.dumps(result.get("error", result), ensure_ascii=False)))
    return result


def listening(port):
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        return True
    except OSError:
        return False


def archicads():
    """The ports Archicad instances listen on."""
    with ThreadPoolExecutor(len(PORTS)) as pool:
        return [port for port, up in zip(PORTS, pool.map(listening, PORTS)) if up]


def fetch(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "archicad-tapir-skill"}),
                                timeout=timeout) as response:
        return response.read()


def latest_tapir():
    return json.loads(fetch("https://api.github.com/repos/%s/releases/latest" % REPO, 30))["tag_name"]


def from_repo(tag, path):
    """A file of the Tapir repository at a release tag, downloaded once into the temp folder."""
    local = os.path.join(tempfile.gettempdir(), "tapir-" + tag, os.path.basename(path))
    if not os.path.exists(local):
        data = fetch("https://raw.githubusercontent.com/%s/%s/%s" % (REPO, tag, path))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as f:
            f.write(data)
    return local


def numbers(version):
    return tuple(int(n) for n in re.findall(r"\d+", version))


def load_installer(tag):
    """Tapir's official installer, a single standard-library script, as a module: its detection is the reference."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tapir_installer", from_repo(tag, INSTALLER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def doctor(port):
    failed = []

    def report(ok, text):
        print({True: "[ok] ", False: "[!!] ", None: "[--] "}[ok] + text)
        if ok is False:
            failed.append(text)

    report(sys.version_info >= (3, 8), "Python %s on %s" % (platform.python_version(), platform.system()))
    if not WINDOWS and sys.platform != "darwin":
        report(None, "Archicad runs on Windows and macOS only; from WSL, run this script with the Windows python.exe")
    if WINDOWS:
        import ctypes
        codepage = ctypes.windll.kernel32.GetACP()
        report(codepage != 65001, "Windows code page %d" % codepage + (
            ": Archicad refuses to start with 'Beta: Use Unicode UTF-8 for worldwide language support'"
            " - switch it off (Region > Administrative > Change system locale) and restart Windows" if codepage == 65001 else ""))

    latest = None
    try:
        latest = latest_tapir()
        report(True, "latest Tapir release: " + latest)
        installer = load_installer(latest)
        found = installer.DetectArchicadInstallations()
        for a in found:
            has = installer.IsTapirInstalled(a.addOnsFolderPath)
            report(True if has else None, "Archicad %d in %s: Tapir %s" % (
                a.version, a.installPath, "installed" if has else "not installed (tapir.py install --versions %d)" % a.version))
        if not found:
            report(None, "no Archicad installation found in the usual places (Tapir needs Archicad 25 or later)")
    except Exception as e:
        report(None, "cannot reach GitHub (%s): install and help need github.com - behind a proxy set HTTPS_PROXY" % e)

    ready = False
    ports = archicads()
    for p in ports:
        try:
            info = call("API.GetProductInfo", port=p, timeout=10)
        except Exception as e:
            report(False, "port %d: something listens there but does not answer like Archicad (%s)" % (p, e))
            continue
        name = "port %d: Archicad %s %s build %s" % (p, info.get("version"), info.get("languageCode"), info.get("buildNumber"))
        try:
            version = call("GetAddOnVersion", port=p, timeout=15)["version"]
        except RuntimeError as e:
            report(False, "%s, Tapir not loaded (%s): tapir.py install, then restart Archicad" % (name, e))
            continue
        except Exception:
            report(False, "%s is busy - no answer in 15 s (a long command, a dialog, or the user mid-input); try again" % name)
            continue
        try:
            project = call("GetProjectInfo", port=p, timeout=15)
            where = project.get("projectPath") or project.get("projectName") or "untitled"
        except Exception as e:
            where = "? (%s)" % e
        report(True, "%s, Tapir %s, project %s" % (name, version, where))
        ready = True
        if latest and numbers(version) < numbers(latest):
            report(None, "Tapir %s is out: quit Archicad (tapir.py quit), tapir.py install, start it again" % latest)
    if not ports:
        report(False, "no Archicad listens on ports %d-%d: start Archicad and open a project, or tapir.py open <project>"
               % (PORTS[0], PORTS[-1]))
    print("ready" if ready and not failed else "not ready - fix the [!!] lines, then run doctor again")
    return 0 if ready and not failed else 1


def install(options):
    """Tapir's official installer in console mode, its own options passed through."""
    command = [sys.executable, "-I", "-X", "utf8", from_repo(latest_tapir(), INSTALLER), "--console"] + options
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(done.stdout + done.stderr, end="")
    if done.returncode and WINDOWS and "Permission denied" in done.stdout + done.stderr:
        if archicads():
            print("Archicad is running and locks the add-ons it loaded: quit it (tapir.py quit), then install again.")
        else:
            print("The Add-Ons folder needs administrator rights: retrying elevated - click Yes on the Windows prompt.")
            return as_admin(command)
    return done.returncode


def as_admin(command):
    """Windows: runs the command once more behind the UAC prompt and returns its exit code."""
    folder = tempfile.mkdtemp(prefix="tapir-install-")
    log, script = os.path.join(folder, "log.txt"), os.path.join(folder, "run.cmd")
    with open(script, "w", encoding="utf-8") as f:           # chcp first, so that cmd reads the paths as UTF-8
        f.write('@chcp 65001 >nul\r\n@%s > "%s" 2>&1\r\n' % (subprocess.list2cmdline(command), log))
    code = subprocess.call(["powershell", "-NoProfile", "-Command",
                            "exit (Start-Process -FilePath '%s' -Verb RunAs -Wait -PassThru -WindowStyle Hidden).ExitCode"
                            % script.replace("'", "''")])
    if not os.path.exists(log):
        print("The administrator prompt was declined.")
        return code or 1
    with open(log, encoding="utf-8", errors="replace") as f:
        print(f.read(), end="")
    return code


def tapir_docs(port):
    """Tapir's commands and schema definitions, of the version Archicad runs (else of the latest release)."""
    try:
        tag = call("GetAddOnVersion", port=port, timeout=5)["version"]
    except Exception:
        tag = latest_tapir()
    parsed = []
    for name in ("command_definitions.js", "common_schema_definitions.js"):
        with open(from_repo(tag, "docs/archicad-addon/" + name), encoding="utf-8") as f:
            text = f.read()
        parsed.append(json.loads(text[text.index("=") + 1:].strip().rstrip(";")))      # var gCommands = [...];
    return tag, parsed[0], parsed[1]


def show_help(name, port):
    if name and name.startswith("API."):
        print("A built-in Archicad command: https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/")
        return 0
    tag, groups, definitions = tapir_docs(port)
    commands = {c["name"]: c for g in groups for c in g["commands"]}
    if not name:
        for g in groups:
            print(g["name"])
            for c in g["commands"]:
                print("  %-38s %s" % (c["name"], " ".join(c["description"].split()).split(". ")[0]))
        print("Tapir %s, %d commands. tapir.py help <Command> shows its parameters and result." % (tag, len(commands)))
        return 0
    if name in commands:
        c = commands[name]
        print("%s - Tapir %s, command since %s\n%s" % (name, tag, c["version"], c["description"]))
        shown = {"parameters": c["inputScheme"], "result": c["outputScheme"]}
    elif name.lstrip("#/") in definitions:
        shown = {"#/" + name.lstrip("#/"): definitions[name.lstrip("#/")]}
    else:
        import difflib
        close = difflib.get_close_matches(name, list(commands) + list(definitions), 6, 0.4)
        print("%s is neither a command nor a definition of Tapir %s. Close: %s" % (name, tag, ", ".join(close) or "-"))
        return 1
    linked = sorted(set(re.findall(r'"#/(\w+)"', json.dumps(shown))) - {k[2:] for k in shown})
    shown.update(("#/" + n, definitions.get(n)) for n in linked)                   # one level inline
    for key, schema in shown.items():
        print("%s: %s" % (key, json.dumps(schema, ensure_ascii=False, indent=1)))
    deeper = sorted(set(re.findall(r'"#/(\w+)"', json.dumps(shown))) - {k[2:] for k in shown})
    if deeper:
        print("more: %s  (tapir.py help <name>)" % ", ".join(deeper))
    return 0


def listening_pid(port):
    """Windows: the process listening on the port - Archicad keeps its add-ons locked until that process is gone."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue).OwningProcess"
                          % port], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def running(pid):
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)                     # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87                                 # 87: no such process
    try:
        return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102   # WAIT_TIMEOUT: still runs
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def quit_archicad(port, wait):
    if not listening(port):
        print("no Archicad on port %d" % port)
        return 0
    reopen = ""
    try:
        exe = call("GetArchicadLocation", port=port, timeout=10)["archicadLocation"]
        project = call("GetProjectInfo", port=port, timeout=10)
        reopen = ('  Reopen: tapir.py open "%s" --exe "%s"' % (project["projectPath"], exe)
                  if project.get("projectPath") and not project.get("isTeamwork") else "  Archicad: " + exe)
    except Exception:
        pass
    pid = listening_pid(port) if WINDOWS else None   # ponytail: macOS waits on the port only; nothing locks there
    try:
        call("QuitArchicad", port=port, timeout=30)
    except RuntimeError as e:
        print("Archicad refused to quit: %s" % e)
        return 1
    except OSError:
        pass                                       # the connection can drop, or wait on the save dialog
    deadline = time.time() + wait
    while time.time() < deadline:
        if not listening(port) and not (pid and running(pid)):
            print("Archicad quit." + reopen)
            return 0
        time.sleep(1)
    print("Archicad still runs after %d s - it is probably asking whether to save; the user has to answer it." % wait + reopen)
    return 1


def project_port(path):
    """The port of the Archicad that has this project open."""
    for port in archicads():
        try:
            found = call("GetProjectInfo", port=port, timeout=5).get("projectPath", "")
        except Exception:
            continue
        if found and os.path.normcase(os.path.abspath(found)) == os.path.normcase(path):
            return port
    return None


def open_project(path, exe, wait):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        sys.exit("no such project file: " + path)
    port = project_port(path)
    if port:
        print("already open on port %d" % port)
        return 0
    others = archicads()
    if others:
        print('Archicad already runs on port %d. Open the project in it:  tapir.py call OpenProject projectFilePath="%s"'
              '  - or quit it first (tapir.py quit).' % (others[0], path))
        return 1
    if exe and exe.endswith(".app"):
        subprocess.Popen(["open", "-a", exe, path])
    elif exe:
        detach = {"creationflags": 0x00000208} if WINDOWS else {"start_new_session": True}  # DETACHED | NEW_GROUP
        subprocess.Popen([exe, path], close_fds=True, **detach)
    elif WINDOWS:
        os.startfile(path)                         # the Archicad this file type opens with
    else:
        subprocess.Popen(["open", path])
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(5)
        port = project_port(path)
        if port:
            print("open on port %d: %s" % (port, path))
            return 0
    print("Archicad did not answer for the project in %d s. Probably a dialog waits on screen: project recovery after a"
          " crash, licence, missing libraries, file conversion." % wait)
    return 1


def dbwin(out):
    """Windows: prints what processes send to OutputDebugString (a C++ add-on's DBPrintf lines) until stopped.
    Only one listener can run; it sees only lines sent after it started; it loses some while Archicad is busy;
    a debugger attached to Archicad takes the lines instead."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.restype = kernel.CreateFileMappingW.restype = wintypes.HANDLE
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    ready = kernel.CreateEventW(None, False, True, "DBWIN_BUFFER_READY")
    if ctypes.get_last_error() == 183:                                       # ERROR_ALREADY_EXISTS
        sys.exit("another debug output listener runs (DebugView, another dbwin): only one of them gets the lines")
    data = kernel.CreateEventW(None, False, False, "DBWIN_DATA_READY")
    mapping = kernel.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 4096, "DBWIN_BUFFER")
    buffer = kernel.MapViewOfFile(mapping, 0x0004, 0, 0, 4096)
    if not (ready and data and buffer):
        sys.exit("cannot open the DBWIN buffer")
    sink = open(out, "a", encoding="utf-8") if out else sys.stdout
    print("listening", file=sink, flush=True)
    while True:                # keep the handshake as it is: a second BUFFER_READY per message desynchronises it
        kernel.SetEvent(ready)
        if kernel.WaitForSingleObject(data, 30000) != 0:
            continue
        pid = ctypes.c_uint32.from_address(buffer).value
        text = ctypes.string_at(buffer + 4).decode("mbcs", "replace").rstrip("\r\n")
        if text:
            print("[%s][%d] %s" % (time.strftime("%H:%M:%S"), pid, text), file=sink, flush=True)


def parameters(args):
    """Command parameters from the command line: JSON, @file.json, - (stdin) or key=value pairs."""
    if not args:
        return None
    if len(args) == 1:
        if args[0] == "-":
            return json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if args[0].startswith("@"):
            with open(args[0][1:], encoding="utf-8-sig") as f:
                return json.load(f)
        if args[0].lstrip().startswith(("{", "[")):
            return json.loads(args[0])
    pairs = {}
    for a in args:
        key, sep, value = a.partition("=")
        if not sep:
            sys.exit("cannot read parameter %r: give JSON, @file.json, - or key=value" % a)
        try:
            pairs[key] = json.loads(value)
        except ValueError:
            pairs[key] = value
    return pairs


def outline(value):
    """A result without its content: list lengths and keys."""
    if isinstance(value, list):
        return "%d items" % len(value)
    if isinstance(value, dict):
        return {k: outline(v) if isinstance(v, (list, dict)) else v for k, v in value.items()}
    return value


def main(args):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # names are rarely ASCII, consoles rarely UTF-8
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, args = args[0], args[1:]
    if command == "install":                                        # every option is the installer's
        return install(args)

    def option(name, default=None):
        if name not in args:
            return default
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value

    port, out, exe = int(option("--port", PORT)), option("--out"), option("--exe")
    timeout, wait = float(option("--timeout", 600)), option("--wait")
    if command == "doctor":
        return doctor(port)
    if command == "help":
        return show_help(args[0] if args else None, port)
    if command == "call" and args:
        try:
            result = call(args[0], parameters(args[1:]), port, timeout)
        except RuntimeError as e:
            sys.exit(str(e))
        except OSError as e:
            sys.exit("no answer on port %d (%s). Archicad not running, no project open, or busy with a long command"
                     " or a dialog? Try: tapir.py doctor" % (port, e))
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if not out:
            print(text)
            return 0
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %s: %s" % (out, json.dumps(outline(result), ensure_ascii=False)))
        return 0
    if command == "quit":
        return quit_archicad(port, float(wait or 180))
    if command == "open" and args:
        return open_project(args[0], exe, float(wait or 900))
    if command == "dbwin" and WINDOWS:
        return dbwin(out)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
