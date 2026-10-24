---
name: archicad-tapir
description: Connect to a running Archicad through the Tapir add-on's JSON API and work with it - check the setup for a first-time user, install or update Tapir, query and edit the open model (elements, properties, layers, stories, selection, views), run an add-on's own JSON commands, quit and restart Archicad on a project, capture a C++ add-on's debug output, and verify the results during add-on development and acceptance testing. Use whenever the user wants the agent to work with Archicad, Tapir or the Archicad JSON API (Windows or macOS, Archicad 25 or later).
---

# Archicad through Tapir

[Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) is a free Archicad add-on (MIT) that adds a few
hundred commands (`tapir.py help` lists those of the version Archicad runs, or of the latest release when it does not
answer) to the JSON API every Archicad serves on `http://127.0.0.1:19723`. Everything here goes through one script
next to this file, `scripts/tapir.py` (Python 3.8+, standard library only). Always start it through Python with its
full path - `python "<this skill>/scripts/tapir.py" ...` (section 1 says which Python command) - never by its path
alone: on Windows that can start the Microsoft Store's `python3` placeholder ("Python was not found" although Python
is installed), and on macOS the file is not executable. Below, and in what tapir.py prints, `tapir.py` stands for
that whole command.

The agent has to run on the computer Archicad runs on (Claude Code, Codex CLI or app, Cursor, ...); a cloud sandbox
cannot reach it. The port has no password, and Archicad can accept connections from other computers, not only from
127.0.0.1: keep the firewall blocking incoming connections to Archicad (if Windows asks when Archicad first starts, do
not allow it). This skill uses only 127.0.0.1, which the firewall does not filter.

## 1. Check the environment first

```
python "<this skill>/scripts/tapir.py" doctor
```

Use `python3` on macOS, `py -3` on Windows when `python` is missing. With no Python at all, ask the user to install
Python 3 (python.org, or `winget install Python.Python.3.13`), then try `py -3` - it finds the new Python without a
restart; if `py` is not found either, ask the user to restart the agent: a running agent keeps the PATH it started
with. On macOS, python.org's Python downloads nothing (CERTIFICATE_VERIFY_FAILED) until the user runs its
`Install Certificates.command` (in its folder in Applications) once. Every `[!!]` line says what to do:

- **No Archicad listens** - ask the user to start Archicad and open a project, or run `tapir.py open <project.pln>`
  in the background (section 4: it waits up to 15 minutes). Archicad runs commands only once a project is open. On a
  dialog before that (such as "This project is in use") it may already listen and answer error 4001 "no open
  project": `doctor` then names the window, as below.
- **Archicad runs but answers on no port**, or **with no project open** - it is still starting ('no window yet': wait
  a minute and run `doctor` again), or a dialog waits (project recovery, a project in use, licence); on Windows
  `doctor` names the window. Tell the user what it shows and let them answer it; never start a second Archicad.
- **Archicad crashed** (a call ends in "dropped the connection", or `doctor` names the Bug Reporter) - tell the user
  at once: changes since the last save are gone unless Project Recovery restores them at the next start. Do not send
  the command that was running again.
- **Tapir not loaded** - `tapir.py install` runs Tapir's official installer for every Archicad it finds
  (`--versions 28` for one, `--dryRun` to only show what it would do). For an Archicad under Program Files, Windows
  shows an administrator prompt: tell the user to click Yes. On macOS, when the Add-Ons folder is not writable, a
  system dialog asks for an administrator's name and password and `install` prints nothing until it is answered: tell
  the user. Archicad has to be restarted to load Tapir - by the user: `tapir.py quit` needs Tapir loaded, so ask
  them to save, quit and start it again.
- **A newer Tapir is out** (a `[--]` line; `doctor` still says `ready`) - optional: mention it, and update only if the
  user wants to. Windows locks loaded add-ons, so then they save, Archicad is quit (section 4), `tapir.py install`,
  start it again. `install` passes every option to Tapir's installer: leave out its update mode (`--addOnFile`,
  `--archicadPort`), which quits Archicad itself as `quit` does (on Archicad 28 without saving or asking).
- **Busy** - Archicad is opening a project, runs a long command or shows a dialog, or the user is mid-input. Wait, or
  ask the user to look.
- **Connecting to 127.0.0.1 is not allowed** - an agent sandbox that cuts the network (such as Codex's default one)
  blocks 127.0.0.1 too, and every port looks closed. Run `tapir.py` with network access (in Codex: approve running it
  outside the sandbox, or set `network_access = true` under `[sandbox_workspace_write]`), then `doctor` again.

Run `doctor` again until it prints `ready`. `install` and `help` download from github.com and
raw.githubusercontent.com, Tapir's installer also from release-assets.githubusercontent.com: a network that blocks
one fails there (behind a proxy, set `HTTPS_PROXY`; CERTIFICATE_VERIFY_FAILED on macOS: see above).

## 2. Run commands

```
tapir.py help                                   # every Tapir command of the version Archicad runs (of the latest
                                                # release when it does not answer: the last line names the version)
tapir.py help GetDetailsOfElements              # one command: parameters, result, the definitions they use
tapir.py call GetProjectInfo
tapir.py call GetElementsByType elementType=Wall
tapir.py call GetDetailsOfElements "@ids.json" --out details.json
tapir.py call API.GetProductInfo                # Archicad's built-in commands
tapir.py call MyAddOn/RunCheck '{"level": 2}'   # any add-on's own JSON command: Namespace/Name
```

- Look the schema up with `help` before writing parameters, and copy enum values from it exactly. Archicad rejects an
  unknown or missing key and a value of the wrong type with error 4002, whose message names the path that failed -
  but it does not check what the schema reaches through a `$ref` (`#/...`): enum values and the items of a list. A
  misspelt `elementType` comes back with Tapir's own code and message; a misspelt `filters` value is dropped without a
  word, so `GetElementsByType` returns every element of the type (and a number as `elementType` every element of the
  project); an unknown `fields` name returns `{}` (below); an id without its `elementId` / `attributeId` wrapper
  fails per item. After a filtered call, check that it returned fewer than the same call without the filter.
- Parameters: either `key=value` pairs (values read as JSON when they parse) or one JSON object - inline,
  `"@file.json"` (in double quotes: PowerShell reads a bare `@name` as splatting) or `-` for stdin - not both. Never a
  bare list (say, a file of element ids): it crashes Archicad, so `tapir.py` refuses it - wrap it as
  `{"elements": [...]}`. Windows PowerShell 5.1 and cmd.exe strip the double quotes out of inline JSON, and 5.1 pipes
  non-ASCII text as `?`: there, use `key=value` for plain values and a UTF-8 `"@file.json"` for a list or object (or
  write each inner quote as `\"`).
- `--out file.json` writes the result as UTF-8 and prints only its outline (list lengths, with how many items are
  empty or failed - open the file for their messages). Use it for anything large; PowerShell's `>` would write UTF-16.
- `tapir.py` prints UTF-8. Piped or captured in PowerShell (`| Select-String`, `$x = ...`, `| ConvertFrom-Json`) or
  cmd (`| findstr`), non-ASCII names come out wrong - on Chinese or Japanese Windows as plausible but different
  characters. Put `[Console]::OutputEncoding = [Text.UTF8Encoding]::new();` (PowerShell) or `chcp 65001 >nul &` (cmd)
  in front of the same command line, or use `--out`. In cmd, `chcp 65001` fixes only how the output looks: `findstr`
  and `find` still never match a non-ASCII word typed on their command line - search with PowerShell (prefix above)
  or Python instead.
- A call waits 600 s for its answer (`--timeout SECONDS`; `call(..., timeout=...)` in Python). A timeout - or the
  agent's own tool timeout, often shorter - only stops the waiting: Archicad finishes the command anyway, and sending
  it again runs it twice (duplicate elements, a move applied twice). Run a long add-on command in the background with
  a larger `--timeout`. After a timeout, wait until `doctor` says `ready`, then read the model (section 3) to see what
  it did.
- Elements are `{"elementId": {"guid": "..."}}`. Commands that take elements want `{"elements": [...]}` in exactly
  the form `GetElementsByType` and `GetSelectedElements` return them.
- More than a couple of calls: write a Python script. `call` returns the result and raises `RuntimeError` when the
  command failed - whether Archicad says so or Tapir answers with a bare `{"error": {...}}`. One element that cannot
  be read (e.g. a GUID that no longer exists) does not fail the call: its entry in the result list is
  `{"error": {...}}` instead of its data, even where `help` does not say so - check before reading its fields. In
  property and classification reads (`GetPropertyValuesOfElements`, `GetClassificationsOfElements`) one value of an
  element fails the same way, one level down. Send element lists in batches of a few hundred.

```python
import sys; sys.path.insert(0, r"<this skill>/scripts")
from tapir import call
sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows: else non-ASCII to a pipe may raise an error
walls = call("GetElementsByType", {"elementType": "Wall"})["elements"]
for i in range(0, len(walls), 300):
    batch = {"elements": walls[i:i + 300], "fields": ["floorIndex", "layerIndex"]}
    for d in call("GetDetailsOfElements", batch)["detailsOfElements"]:
        if "error" not in d:
            print(d["floorIndex"], d["layerIndex"])
```

List in `fields` only what you read: the others are not computed, which skips the floor plan polygons that take most
of the time of a batch read (about 9 times faster in one measured case). Without `fields` every field comes back; a
Tapir version that lacks the parameter rejects it (error 4002). `fields` takes only the top-level names that
`help GetDetailsOfElements` lists under ElementDetailsField (type, id, floorIndex, layerIndex, drawIndex, details,
floorPlanPolygons, hotlinkId). Any other name, such as `thickness`, is not rejected: every element comes back `{}`
(`--out` counts them as empty). Ask for `details` and read such values out of it, and check that the first result has
the keys you asked for.

## 3. Acceptance: trust only what you read back

`success: true` - for a whole command, or per element in `executionResults` - only means nothing failed, not that
anything changed. After every change, read the model again and compare it with what was expected:

- counts: `GetElementsByType` per type, split by story through the `floorIndex` of `GetDetailsOfElements` - it is
  the `index` of a story in `GetStories`, so match on it: with stories below ground the list starts at a negative
  index and `stories[floorIndex]` names the wrong story without an error. A story's `name` can be empty: show its
  index and level too.
- values: `GetDetailsOfElements` (geometry, layer, story; `fields` limits the output and the time it takes),
  `GetPropertyValuesOfElements`, `GetClassificationsOfElements`, `Get3DBoundingBoxes`
- what the user sees: `GetSelectedElements`, `GetCurrentWindowType`, `HighlightElements`

Report the numbers before and after and every difference, not a sample. When Archicad was reopened, first check that
the previous result is still there - a project that was not saved has lost it.

## 4. Developing an add-on: the rebuild loop

Archicad keeps every add-on it loaded locked, so building a new `.apx` (or updating Tapir) needs it closed:

```
tapir.py quit             # no save, no question; prints the command that reopens the same project
<build the add-on>
tapir.py open "C:\Projects\test.pln" --exe "C:\Program Files\GRAPHISOFT\Archicad 28\Archicad.exe"
tapir.py call MyAddOn/RunCheck        # then check the model as in section 3
```

- `quit` closes Archicad **without saving, and does not ask** (seen on Archicad 28; Tapir calls Archicad's quit
  differently on 25-27, which may show the save prompt instead - then `quit` ends saying a dialog may wait: tell the
  user to answer it, do not end Archicad): unsaved changes are lost. Ask the user before quitting unless they told you
  to; to keep their changes they save first (or tell you to `call SaveProject`) - never save their project on your
  own.
- One Archicad at a time: each further one takes the next port (19724, ...; `--port`) and a lot of memory. `open`
  refuses while another one runs - on Windows also one that is still starting or waits on a dialog and has no port
  yet. `call OpenProject projectFilePath=...` switches projects inside a running Archicad, but like `quit` it drops
  the open project's unsaved changes without asking (seen on Archicad 28): ask the user first, or have them save. A
  dialog the project raises on opening (missing libraries, file conversion) holds the call until someone answers it.
- `open` on a large project takes minutes and waits up to 15 minutes (`--wait`, default 900 s); `quit` up to about 4
  (give it a 5-minute tool timeout). Run `open` in the background - an agent's tool call is usually cut off sooner,
  and a foreground one shows nothing until it returns - and let it end by itself. Meanwhile read its output, or run
  `doctor`, about every minute (wait with the agent's own monitor or background-wait feature, not a foreground sleep -
  some agents refuse one): `open` prints each window once, when it appears, then waits silently, so a dialog line
  means Archicad waits for the user - tell them at once. If a tool call was cut off anyway, Archicad keeps starting -
  run `doctor`, not `open` again. On Windows `open` prints the windows Archicad shows meanwhile: 'Archicad NN',
  '<name> - Archicad NN' or 'Untitled - Archicad NN' is its main window, also with a docked palette's label after it
  (e.g. '<name> - Archicad NN: Main:'), and '... Palette' a palette - neither waits for an answer; a progress window
  (such as Opening Project) means it is loading; a dialog is listed with its text after its title, e.g.
  `Information: "<file>" This project is in use by <user>` (a bare `Information` is that dialog too). On Windows, if
  the Archicad it started ends before the project is open (a crash - often of an add-on just built - or the user
  closed it), `open` says so and exits 1: tell the user, then run `doctor`. An end in its first seconds, and any end
  on macOS, is not seen: `open` waits out `--wait`, then blames a dialog. `doctor` meanwhile finds no Archicad on
  Windows; on macOS it cannot tell a crash from a start, so if it takes long, ask the user whether Archicad is still
  on screen. After a crash or a forced end expect two in turn: Project Recovery (Cancel goes on without recovering but
  keeps the recovery data, so it asks again at every start - also after a clean `quit` - until the data is opened or
  deleted), then "This project is in use by <you>" - the lock the crash left next to the project, which Open with
  Exclusive Access is meant for when the name is the user's own and no other Archicad of theirs has the project open;
  otherwise Don't Open. Also: licence, missing libraries, file conversion. Tell the user what is on screen and let
  them choose; do not press buttons in their project.
- Give the add-on a JSON command for everything worth testing - a class deriving from `API_AddOnCommand`, registered
  in `Initialize()` with `ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler` (Archicad 27+;
  `ACAPI_Install_AddOnCommandHandler` before) - so the agent runs it with `call Namespace/Name` instead of a click.
  Have it answer with what its dialogs would have said instead of showing them, and make a command that builds things
  safe to run twice (skip what already exists).
- Debug output of a C++ add-on (`DBPrintf`, `OutputDebugString`; Windows): start `tapir.py dbwin --out dbwin.log` in
  the background **before** triggering the command, and read the file afterwards. Only one listener can run; it sees
  only lines sent after it started; it drops lines while Archicad is busy (compare a summary count with the detail
  lines); a debugger attached to Archicad takes the lines instead. Diagnostics that matter belong in a file the
  add-on writes. Debug text arrives in the system ANSI code page: characters outside it (e.g. CJK on a Western code
  page) come through as `?`, so write such diagnostics to a UTF-8 file. It runs until stopped: start it with the
  agent's own background-task feature, and when done stop that task or the pid on its first line
  (`taskkill -PID <pid> -F`, the form that works in every shell) - never `taskkill /IM python.exe`, which ends every
  Python on the machine. When it refuses because another listener runs, look for a leftover one, in PowerShell:
  `Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object CommandLine -like '*dbwin*'`.
- Speed (Windows): while Archicad's window is minimised or fully covered (the agent's own window counts), or the
  screen is off, Windows may lower its priority and, on CPUs with efficiency cores, run it on those (power
  throttling): a long command can take up to about twice as long. Keep Archicad's window visible for long or timed
  runs, or opt the process out for its lifetime: `SetProcessInformation` with `ProcessPowerThrottling`, ControlMask
  `PROCESS_POWER_THROTTLING_EXECUTION_SPEED`, StateMask 0 (no administrator rights needed).

## 5. Traps that fail silently

- **Hidden layers**: elements on a hidden layer cannot be selected, and `ChangeSelectionOfElements` still reports
  success. Show the layer first - `CreateLayers` with `overwriteExisting: true` changes an existing layer (there is no
  modify-layer command). `GetLayers` (field `isHidden`) says which layers are hidden.
- **Selection**: when one `ChangeSelectionOfElements` call has an element in both lists,
  `removeElementsFromSelection` wins; send two calls. Removing thousands at once can freeze Archicad for many minutes:
  batch about 100, or ask the user to press Esc. Count with `GetSelectedElements` afterwards.
- **Window**: with the 3D window in front, deleting or selecting 2D elements (lines, texts, fills) reports success
  and does nothing. Check `GetCurrentWindowType`, then `ChangeWindow` to the floor plan.
- **Groups**: `DeleteElements` on one element of a group deletes the whole group; `MoveElements` on several members
  moves the group once per member. Look at `GetGroupsOfElements` first and act once per group. Suspend Groups does not
  change this.
- **One command at a time**: commands run on Archicad's main thread, so a long command makes every other call time
  out; a modal dialog or the user's input makes them fail with error 4001, whose message names the reason (no open
  project / ongoing user input / there is an open modal dialog: <name>): ask the user to finish it or press Esc.
  Don't point two agents at one Archicad.
- **Misspelt filters**: an unknown `filters` value is ignored, not rejected - the call returns what it would without
  it (section 2).
- **Layer numbers**: element details give `layerIndex`; `GetAttributesByType` with `attributeType: Layer` gives each
  layer's `index`. Match on it, not on list order. Layer 1 is the Archicad Layer: its `name` comes back as the control
  character U+0014 (seen on Archicad 28), which prints as nothing or `¶` - show it as "Archicad Layer".
- **Arcs and circles**: `GetElementsByType` with `Arc` returns the circles too (every one of them, in a measured
  Archicad 28 project); their details say type `Arc` with `begAngle` equal to `endAngle` - a full circle, not a
  zero-length arc. `Circle` returns only the circles. Read one list, not both: adding the two counts every circle twice.
- **2D details**: a text's `height` is in paper millimetres; in the model it scales with the drawing scale (model
  metres = height x scale / 1000, so height x 0.1 at 1:100). `help GetDetailsOfElements` does not link the details
  of lines, arcs and fills: ask for `help LineDetails`, `help ArcDetails`, `help HatchDetails`.
- **Imported drawings**: a merged DWG can be tens of thousands of elements. Moving or deleting them through Tapir is
  slow and gets refused mid-way when the user touches Archicad; ask the user to do it in Archicad.
- **Windows code page 65001** (the "Beta: Use Unicode UTF-8" option): Archicad refuses to start. `doctor` checks it.
- Before anything destructive, count; afterwards count again, and ask for Ctrl+Z at once if more changed than
  intended.
