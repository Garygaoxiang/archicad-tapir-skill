#!/usr/bin/env python3
"""Drive a running Archicad through the Tapir add-on's JSON API. Python 3.8+, standard library only.

  tapir.py doctor                       check Python, Archicad and Tapir; every [!!] line says what to do
  tapir.py install [installer options]  install or update Tapir with its official installer, latest release
                                        (--versions 28   --dryRun   --uninstall   --addOnsFolder DIR)
  tapir.py help [Command|Definition]    the commands of the Tapir version Archicad runs (of the latest release
                                        when it does not answer), or one command's schema
  tapir.py call Command [params]        run a Tapir command; API.Name runs a built-in one, Namespace/Name an add-on's
  tapir.py quit                         quit Archicad WITHOUT saving (it does not ask) and wait until it is gone
  tapir.py open PROJECT [--exe PATH]    start Archicad on a project and wait until it answers for it; on Windows
                                        it lists the windows (dialogs) Archicad shows meanwhile
  tapir.py dbwin                        Windows: print OutputDebugString lines (C++ add-on logs) until stopped

params   either key=value pairs (each value read as JSON when it parses) or one JSON object: inline, "@file.json"
         or - for stdin
options  go after the subcommand, a space before each value (tapir.py call X --port 19724)
         --port N (default 19723 or $ARCHICAD_PORT)   --out FILE (call, dbwin: write UTF-8 there)
         --timeout SECONDS (call; default 600 - it only stops the waiting, the command goes on in Archicad)
         --wait SECONDS (quit 180, open 900)

From Python:  sys.path.insert(0, "<this folder>"); from tapir import call
              walls = call("GetElementsByType", {"elementType": "Wall"})["elements"]
"""
import csv
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
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # a system proxy must not get 127.0.0.1


def call(command, params=None, port=PORT, timeout=600):
    """Runs one command and returns its result; raises RuntimeError carrying Archicad's or Tapir's error.
    One element's {"error": ...} inside a result list is returned in its place, for the caller to check.
    "API.X" is a built-in command, "Namespace/X" an add-on's own, a bare name a Tapir command."""
    if params is not None and not isinstance(params, dict):       # a list or a string crashes Archicad 28 with Tapir
        raise ValueError("%s: parameters must be one JSON object {...}, not a %s - a list of elements goes in"
                         ' {"elements": [...]}' % (command, type(params).__name__))
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
    with LOCAL.open(request, timeout=timeout) as response:
        reply = json.loads(response.read().decode("utf-8"))
    if not reply.get("succeeded"):
        raise RuntimeError("%s: %s" % (command, json.dumps(reply.get("error"), ensure_ascii=False)))
    result = reply.get("result") or {}
    result = result.get("addOnCommandResponse", result)
    if isinstance(result, dict) and (result.get("success") is False        # Tapir's failed ExecutionResult
                                     or set(result) == {"error"}):         # and its bare ErrorItem
        raise RuntimeError("%s: %s" % (command, json.dumps(result.get("error", result), ensure_ascii=False)))
    return result


def timed_out(error):
    """The call got no answer in time - which does not stop the command in Archicad."""
    return isinstance(error, (socket.timeout, TimeoutError)) or "timed out" in str(error)


SANDBOXED = ("connecting to 127.0.0.1 is not allowed (%s) - most likely the agent's sandbox, which blocks the network,"
             " 127.0.0.1 included, so every port looks closed: run tapir.py with network access (in Codex: approve"
             " running it outside the sandbox), then run doctor again")


class Blocked(Exception):
    """Connecting to 127.0.0.1 is forbidden - not the same as a closed port."""


def listening(port):
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        return True
    except PermissionError as e:                   # WSAEACCES / EPERM: a sandbox forbids it, the port may well be open
        raise Blocked(SANDBOXED % e)
    except OSError:
        return False


def archicads():
    """The ports Archicad instances listen on."""
    with ThreadPoolExecutor(len(PORTS)) as pool:
        return [port for port, up in zip(PORTS, pool.map(listening, PORTS)) if up]


def tasks(image):
    """Windows: the pids of the running processes of this image name."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + image, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace").stdout
    except OSError:                                # tasklist blocked by policy: only the ports are known then
        return []
    return [int(row[1]) for row in csv.reader(out.splitlines()) if len(row) > 1 and row[1].isdigit()]


def archicad_windows():
    """Windows: {pid: titles of its visible windows} of every running Archicad. Archicad runs commands only once a
    project is open, so one that is starting or waits on a dialog has no port yet, or answers 'no open project'.
    Elsewhere: {}."""
    return {pid: window_titles(pid) for pid in tasks("Archicad.exe")} if WINDOWS else {}


def bug_reporter():
    """Windows: whether Archicad's Bug Reporter runs - what is left of an Archicad that crashed. Elsewhere: False."""
    return WINDOWS and bool(tasks("GSReport.exe"))


def window_titles(pid):
    """Windows: the titles of a process's visible top-level windows, a dialog's with its first line of text."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32")
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    titles = []

    def text(window, size=512):
        buffer = ctypes.create_unicode_buffer(size)
        user.GetWindowTextW(window, buffer, size)   # another process's stored text: sends no message, cannot hang
        return buffer.value

    def visit(window, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(window):
            title = text(window, 256)
            if title and title != "DGTransparentWindow":                     # an empty helper window of Archicad
                said = []

                def child(control, _):
                    kind = ctypes.create_unicode_buffer(16)
                    user.GetClassNameW(control, kind, 16)
                    if kind.value == "Static" and text(control).strip():
                        said.append(" ".join(text(control).split())[:120])
                    return not said                                          # stop at the first one
                user.EnumChildWindows(window, enum(child), 0)
                titles.append(title + (": " + said[0] if said else ""))
        return True
    user.EnumWindows(enum(visit), 0)
    return titles


def describe(processes):
    """'pid 123 shows: Archicad Project Recovery' for each of archicad_windows()."""
    return "; ".join("pid %d shows: %s" % (pid, ", ".join(titles) or "no window yet")
                     for pid, titles in processes.items())


def shows():
    """describe() of every running Archicad, for a message."""
    return describe(archicad_windows()) or "look at its window"


def no_project(error):
    """Archicad answers but has no project open yet: a dialog before it waits (such as 'This project is in use')."""
    return isinstance(error, RuntimeError) and "no open project" in str(error)


def refuses(error):
    """Error 4001 (invalid program status) for any reason: no project, a modal dialog, the user in the middle of input."""
    return isinstance(error, RuntimeError) and '"code": 4001' in str(error)


REFUSES = ("Archicad answers but refuses commands now (%s; %s): a dialog is open or the user is in the middle of"
           " something - ask them to finish it or press Esc, then try again.")
PORTLESS = ("it is still starting, or a dialog waits for the user (project recovery, a project in use, licence) -"
            " tell them what it shows and let them answer it. Do not start another Archicad.")
CRASHED = ("Changes since the last save are lost unless Project Recovery offers them when Archicad starts again: tell"
           " the user.")


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
    except Exception as e:
        report(None, "cannot reach GitHub (%s): install and help need github.com - behind a proxy set HTTPS_PROXY" % e)
    if latest:
        try:
            installer = load_installer(latest)
            found = installer.DetectArchicadInstallations()
            for a in found:
                has = installer.IsTapirInstalled(a.addOnsFolderPath)
                report(True if has else None, "Archicad %d in %s: Tapir %s" % (
                    a.version, a.installPath, "installed" if has else "not installed (tapir.py install --versions %d)" % a.version))
            if not found:
                report(None, "no Archicad installation found in the usual places (Tapir needs Archicad 25 or later)")
        except Exception as e:
            report(None, "Tapir's installer could not list the Archicad installations (%s); the checks below still apply"
                   % e)

    ready, blocked = False, False
    try:
        ports = archicads()
    except Blocked as e:
        report(False, str(e))
        ports, blocked = [], True
    for p in ports:
        try:
            info = call("API.GetProductInfo", port=p, timeout=10)
        except Exception as e:
            if no_project(e):
                report(False, "port %d: Archicad runs with no project open (%s): %s" % (p, shows(), PORTLESS))
                continue
            if refuses(e):
                report(False, "port %d: " % p + REFUSES % (e, shows()))
                continue
            report(False, "port %d: no answer in 10 s (%s) - Archicad is busy (opening a project, a long command, a dialog, the"
                          " user mid-input) or hangs: wait, or ask the user to look, then run doctor again" % (p, shows())
                   if timed_out(e) else "port %d: something listens there but does not answer like Archicad (%s)" % (p, e))
            continue
        name = "port %d: Archicad %s %s build %s" % (p, info.get("version"), info.get("languageCode"), info.get("buildNumber"))
        try:
            version = call("GetAddOnVersion", port=p, timeout=15)["version"]
        except RuntimeError as e:
            if no_project(e):
                report(False, "%s runs with no project open (%s): %s" % (name, shows(), PORTLESS))
            elif refuses(e):
                report(False, "%s: " % name + REFUSES % (e, shows()))
            else:
                report(False, "%s, Tapir not loaded (%s): tapir.py install, then the user restarts Archicad (tapir.py"
                              " quit needs Tapir loaded)" % (name, e))
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
            report(None, "Tapir %s is out (optional): to update, save, quit Archicad (tapir.py quit does not save),"
                         " tapir.py install, start it again" % latest)
    none = not ports and not blocked
    silent = archicad_windows() if none else {}
    if silent:
        report(False, "Archicad runs but answers on no port (%s): %s" % (describe(silent), PORTLESS))
    elif none and bug_reporter():
        report(False, "Archicad crashed - its Bug Reporter (GSReport) is open; the user sends the report or closes it. "
                      + CRASHED)
    elif none:
        report(False, "no Archicad listens on ports %d-%d: start Archicad and open a project, or tapir.py open <project>"
               % (PORTS[0], PORTS[-1]))
    print("ready" if ready and not failed else "not ready - fix the [!!] lines, then run doctor again")
    return 0 if ready and not failed else 1


NO_TAPIR = ("Tapir is not loaded in this Archicad, so tapir.py cannot quit it: ask the user to save and quit Archicad,"
            " then start it again - it loads Tapir at its start.")


def no_tapir(port):
    """Archicad answers on the port but has no Tapir command (error 4010), so it locks no Tapir file."""
    try:
        call("GetAddOnVersion", port=port, timeout=10)
    except RuntimeError as e:
        return '"code": 4010' in str(e)
    except Exception:
        pass
    return False


def install(options):
    """Tapir's official installer in console mode, its own options passed through."""
    command = [sys.executable, "-I", "-X", "utf8", from_repo(latest_tapir(), INSTALLER), "--console"] + options
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(done.stdout + done.stderr, end="")
    if done.returncode and WINDOWS and "Permission denied" in done.stdout + done.stderr:
        ports = archicads()                             # one without Tapir (a first install) locks nothing of it
        if [p for p in ports if not no_tapir(p)] or (not ports and archicad_windows()):   # or one with no port yet
            print("Archicad is running and locks the add-ons it loaded: save, quit it (tapir.py quit does not save),"
                  " then install again.")
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
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue)"
                              ".OwningProcess" % port], capture_output=True, text=True).stdout.split()
    except OSError:                                # PowerShell blocked by policy: wait on the port only
        return None
    return int(out[0]) if out and out[0].isdigit() else None


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
        silent = {} if archicads() else archicad_windows()
        if silent:                                 # not a success: its add-ons stay locked until it is gone
            print("Archicad runs but answers on no port (%s): %s Its add-ons stay locked until it is gone."
                  % (describe(silent), PORTLESS))
            return 1
        print("no Archicad on port %d" % port)
        return 0
    reopen = ""
    try:
        exe = call("GetArchicadLocation", port=port, timeout=10)["archicadLocation"]
        project = call("GetProjectInfo", port=port, timeout=10)
        reopen = ('  Reopen: tapir.py open "%s" --exe "%s"' % (project["projectPath"], exe)
                  if project.get("projectPath") and not project.get("isTeamwork") else "  Archicad: " + exe)
    except Exception as e:
        if timed_out(e):                           # a quit sent now would wait in the queue and run once it is free
            print("Archicad does not answer in 10 s (%s) - busy opening a project, with a long command or a dialog. Not"
                  " quitting: a quit sent now would wait, then close it without saving once it is free. Wait until"
                  " tapir.py doctor says ready; if Archicad hangs, the user ends it." % shows())
            return 1
        if no_project(e):
            print("Archicad runs with no project open (%s): %s Its add-ons stay locked until it is gone."
                  % (shows(), PORTLESS))
            return 1
        if refuses(e):                             # the user is at it: do not close it under them without saving
            print(REFUSES % (e, shows()) + " Not quitting.")
            return 1
        if '"code": 4010' in str(e):               # no Tapir: no QuitArchicad to send
            print(NO_TAPIR)
            return 1
    pid = listening_pid(port) if WINDOWS else None   # ponytail: macOS waits on the port only; nothing locks there
    try:
        call("QuitArchicad", port=port, timeout=30)
    except RuntimeError as e:
        print("Archicad runs with no project open (%s): %s Its add-ons stay locked until it is gone."
              % (shows(), PORTLESS) if no_project(e) else "Archicad refused to quit: %s" % e)
        return 1
    except OSError:
        pass                                       # the connection can drop as Archicad goes away
    deadline = time.time() + wait
    while time.time() < deadline:
        if not listening(port) and not (pid and running(pid)):
            print("Archicad quit." + reopen)
            return 0
        time.sleep(1)
    left = describe({pid: window_titles(pid)}) if pid else ""
    print("Archicad still runs after %d s%s: a dialog may wait on screen - a save prompt, which the user answers, or"
          " another one, and once it is answered the pending quit closes Archicad without saving - or it hangs on its"
          " way out: then the user ends it in the Task Manager. Its next start offers project recovery, then may ask"
          " 'This project is in use by <you>' - the lock the ended Archicad left: Open with Exclusive Access is meant"
          " for that; Don't Open if the name is someone else's." % (wait, " (%s)" % left if left else "") + reopen)
    return 1


def project_port(path, unknown=None):
    """The port of the Archicad that has this project open; unknown collects {port: error} of those that did not say."""
    for port in archicads():
        try:
            found = call("GetProjectInfo", port=port, timeout=5).get("projectPath", "")
        except Exception as e:
            if unknown is not None:
                unknown[port] = e
            continue
        if found and os.path.normcase(os.path.abspath(found)) == os.path.normcase(path):
            return port
    return None


# Windows: Archicad is started by a Python that exits at once, so that it is in no process tree of this open - an
# agent that stops a background open ends that whole tree. --exe: DETACHED | NEW_GROUP; else the file's association.
LAUNCH = ("import os, subprocess, sys; a = sys.argv[1:]\n"
          "subprocess.Popen(a, close_fds=True, creationflags=0x00000208) if a[0] else os.startfile(a[1])")


def open_project(path, exe, wait):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        sys.exit("no such project file: " + path)
    unknown = {}
    port = project_port(path, unknown)
    if port:
        print("already open on port %d" % port)
        return 0
    for port, e in sorted(unknown.items()):        # which project these have open is unknown: no reason to quit them
        if no_project(e):
            print("Archicad already runs on port %d with no project open (%s): %s" % (port, shows(), PORTLESS))
            return 1
        if timed_out(e):
            print("Archicad on port %d does not answer in 5 s (%s; busy opening a project, with a long command or a dialog),"
                  " so which project it has open is unknown - perhaps this one. Wait until tapir.py doctor says ready, then"
                  " open again." % (port, shows()))
            return 1
        if refuses(e):
            print("Archicad on port %d: " % port + REFUSES % (e, shows()) + " Which project it has open is unknown"
                  " - perhaps this one - so do not quit it to open this one.")
            return 1
    others = archicads()
    if others:
        print('Archicad already runs on port %d. Switching drops its unsaved changes without asking - ask the user'
              ' first, then: tapir.py quit and open again, or tapir.py call OpenProject projectFilePath="%s"'
              % (others[0], path))
        return 1
    silent = archicad_windows()
    if silent:
        print("Archicad already runs but answers on no port (%s): %s" % (describe(silent), PORTLESS))
        return 1
    if exe and exe.endswith(".app"):
        subprocess.Popen(["open", "-a", exe, path])
    elif WINDOWS:                                  # see LAUNCH
        started = subprocess.run([sys.executable, "-I", "-c", LAUNCH, exe or "", path], capture_output=True,
                                 text=True, errors="replace")
        if started.returncode:
            sys.exit("cannot start %s: %s" % (exe or "the program .pln files open with",
                                              (started.stderr.strip().splitlines() or ["?"])[-1]))
    elif exe:
        subprocess.Popen([exe, path], close_fds=True, start_new_session=True)
    else:
        subprocess.Popen(["open", path])
    print("starting Archicad on %s - a large project takes minutes%s" % (
        path, "; the windows it shows are listed as they appear" if WINDOWS else ""), flush=True)
    start, shown, alive, seen, gone = time.time(), set(), set(), False, 0
    while time.time() < start + wait:
        time.sleep(5)
        port = project_port(path)
        if port:
            print("open on port %d: %s" % (port, path))
            return 0
        processes = archicad_windows()                       # Windows only: {} elsewhere, so the end is not seen there
        for pid in sorted(alive - set(processes)):
            print("  %d s: Archicad (pid %d) ended" % (time.time() - start, pid), flush=True)
        alive, seen = set(processes), seen or bool(processes)
        gone = gone + 1 if seen and not alive and not archicads() else 0
        if gone == 2:                                        # two polls in a row: not a hand-over between processes
            print("Archicad ended %d s after it started, before it opened the project: %s Run doctor before starting"
                  " it again." % (time.time() - start, "it crashed - its Bug Reporter (GSReport) is open; the user"
                                  " sends the report or closes it." if bug_reporter() else
                                  "it crashed or was closed - ask the user."))
            return 1
        for pid, titles in processes.items():                # a dialog shows up here long before any port
            for title in titles:
                key = (pid, re.sub(r"\d+", "#", title))             # a progress text that counts is one window
                if key not in shown:
                    shown.add(key)
                    print("  %d s: Archicad (pid %d) shows '%s'" % (time.time() - start, pid, title), flush=True)
    print("Archicad did not answer for the project in %d s. Probably a dialog waits on screen: project recovery (after a"
          " crash, or data an earlier Cancel kept), 'This project is in use' (the lock a crash or a forced quit leaves),"
          " licence, missing libraries, file conversion. Tell the user what it shows." % wait)
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
        sys.exit("another debug output listener runs (DebugView, or a dbwin left over from an earlier session): only one"
                 " of them gets the lines. A dbwin prints its pid on its first line: stop it with taskkill -PID <pid> -F,"
                 " never by image name")
    data = kernel.CreateEventW(None, False, False, "DBWIN_DATA_READY")
    mapping = kernel.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 4096, "DBWIN_BUFFER")
    buffer = kernel.MapViewOfFile(mapping, 0x0004, 0, 0, 4096)
    if not (ready and data and buffer):
        sys.exit("cannot open the DBWIN buffer")
    sink = open(out, "w", encoding="utf-8") if out else sys.stdout   # a fresh log: its first line is this pid
    for stream in {sink, sys.stdout}:                     # the pid, so that whoever reads the log can stop this one
        print("listening, pid %d" % os.getpid(), file=stream, flush=True)
    while True:                # keep the handshake as it is: a second BUFFER_READY per message desynchronises it
        kernel.SetEvent(ready)
        if kernel.WaitForSingleObject(data, 30000) != 0:
            continue
        pid = ctypes.c_uint32.from_address(buffer).value
        text = ctypes.string_at(buffer + 4).decode("mbcs", "replace").rstrip("\r\n")
        if text:
            print("[%s][%d] %s" % (time.strftime("%H:%M:%S"), pid, text), file=sink, flush=True)


QUOTES = (" - Windows PowerShell 5.1 and cmd.exe strip the double quotes out of inline JSON (cmd.exe has no single"
          ' quotes either): use key=value for plain values and a UTF-8 "@file.json" for a list or object (in double'
          ' quotes: PowerShell reads a bare @name as splatting), or write each inner quote as \\"')


def parameters(args):
    """Command parameters from the command line: JSON, @file.json, - (stdin) or key=value pairs."""
    if not args:
        return None
    if len(args) == 1:
        try:
            if args[0] == "-":
                return json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
            if args[0].startswith("@"):
                with open(args[0][1:], encoding="utf-8-sig") as f:
                    return json.load(f)
            if args[0].lstrip().startswith(("{", "[")):
                return json.loads(args[0])
        except OSError as e:                       # a missing @file is not a connection problem
            sys.exit("cannot read %s: %s" % (args[0][1:] or "stdin", e.strerror or e))
        except ValueError as e:                    # also a file that is not UTF-8
            sys.exit("parameters are not valid JSON (%s): %s%s" % (e, args[0], QUOTES))
    pairs = {}
    for a in args:
        key, sep, value = a.partition("=")
        if not sep:
            sys.exit('cannot read parameter %r: give either key=value pairs or one JSON object (inline, "@file.json"'
                     " or - for stdin), not both%s" % (a, QUOTES if "{" in a or "[" in a else ""))
        try:
            pairs[key] = json.loads(value)
        except ValueError:                         # [OnActualFloor]: the quotes stripped, not a string to send
            if value.lstrip().startswith(("[", "{")):
                sys.exit("cannot read %s as JSON%s" % (a, QUOTES))
            pairs[key] = value
    return pairs


def errors(value):
    """Whether value holds an {"error": {...}} at any depth - e.g. one property of an element in
    GetPropertyValuesOfElements. Not details {"error": "Not yet supported element type"}: that element was read."""
    if isinstance(value, dict):
        return isinstance(value.get("error"), dict) or any(errors(v) for v in value.values())
    return isinstance(value, list) and any(errors(v) for v in value)


def outline(value):
    """A result without its content: list lengths and keys, with how many items are empty or failed."""
    if isinstance(value, list):
        empty = sum(1 for v in value if v == {})   # e.g. fields the command does not know: no error, only {}
        failed = sum(1 for v in value if errors(v))   # one element's error, one of its properties, a failed
        notes = ["%d empty" % empty] * bool(empty) + ["%d failed" % failed] * bool(failed)   # executionResult
        return "%d items" % len(value) + (" (%s)" % ", ".join(notes) if notes else "")
    if isinstance(value, dict):
        return {k: outline(v) if isinstance(v, (list, dict)) else v for k, v in value.items()}
    return value


def main(args):
    for stream in (sys.stdout, sys.stderr):                         # names are rarely ASCII, consoles rarely UTF-8
        stream.reconfigure(encoding="utf-8", errors="replace")
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, args = args[0], args[1:]
    try:
        return run(command, args)
    except Blocked as e:
        sys.exit(str(e))


def run(command, args):
    if command == "install":                                        # every option is the installer's
        return install(args)
    if "-h" in args or "--help" in args:                            # quit --help must not quit Archicad
        print(__doc__)
        return 0

    def option(name, default=None):
        if name not in args:
            return default
        i = args.index(name)
        if i + 1 == len(args) or args[i + 1].startswith("--"):
            sys.exit("%s needs a value" % name)
        value = args[i + 1]
        del args[i:i + 2]
        return value

    def number(name, default, kind=float):
        value = option(name, default)
        try:
            return None if value is None else kind(value)
        except ValueError:
            sys.exit("%s needs a number, not %r" % (name, value))

    port, out, exe = number("--port", PORT, int), option("--out"), option("--exe")
    timeout, wait = number("--timeout", 600), number("--wait", None)
    unknown = [a for a in args if a.startswith("-") and a != "-"]
    if unknown:                                    # quit --port=19724 would quit the Archicad on the default port
        sys.exit("unknown option %s: the options are --port --out --exe --timeout --wait, each followed by a space and"
                 " its value (--port 19724, not --port=19724)" % unknown[0])
    if command == "doctor":
        return doctor(port)
    if command == "help":
        return show_help(args[0] if args else None, port)
    if command == "call" and args:
        params = parameters(args[1:])              # exits on parameters it cannot read, before anything is sent
        try:                                       # so does a --out it cannot write: a run must not be lost to it
            sink = open(out, "a", encoding="utf-8") if out else None
        except OSError as e:
            sys.exit("cannot write %s (%s): nothing was sent" % (out, e.strerror or e))
        try:
            result = call(args[0], params, port, timeout)
        except (RuntimeError, ValueError) as e:
            sys.exit(str(e))
        except OSError as e:
            reason = getattr(e, "reason", e)       # urllib wraps what fails while the request is sent
            if isinstance(reason, PermissionError):
                sys.exit(SANDBOXED % reason)
            if timed_out(e):
                sys.exit("no answer in %g s - Archicad goes on with the command: do not send it again. When tapir.py"
                         " doctor says ready it has finished; read the model to see what it did. Long commands:"
                         " --timeout SECONDS, run in the background." % timeout)
            if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                sys.exit("Archicad dropped the connection during %s (%s) - it has most likely crashed (on Windows its"
                         " Bug Reporter opens) or was closed. Do not send the command again: it may be what crashed"
                         " it. %s Then run tapir.py doctor." % (args[0], reason, CRASHED))
            sys.exit("no answer on port %d (%s). Archicad not running, no project open, or busy with a long command"
                     " or a dialog? Try: tapir.py doctor" % (port, e))
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if not sink:
            print(text)
            return 0
        with sink:                                 # emptied only now: a failed call keeps the previous result
            sink.truncate(0)
            sink.write(text)
        print("wrote %s: %s" % (out, json.dumps(outline(result), ensure_ascii=False)))
        return 0
    if command == "quit":
        return quit_archicad(port, 180 if wait is None else wait)
    if command == "open" and args:
        return open_project(args[0], exe, 900 if wait is None else wait)
    if command == "dbwin" and WINDOWS:
        return dbwin(out)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
