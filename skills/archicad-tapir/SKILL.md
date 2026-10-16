---
name: archicad-tapir
description: Connect to a running Archicad through the Tapir add-on's JSON API and work with it - check the setup for a first-time user, install or update Tapir, query and edit the open model (elements, properties, layers, stories, selection, views), run an add-on's own JSON commands, quit and restart Archicad on a project, capture a C++ add-on's debug output, and verify the results during add-on development and acceptance testing. Use whenever the user wants the agent to work with Archicad, Tapir or the Archicad JSON API (Windows or macOS, Archicad 25 or later).
---

# Archicad through Tapir

[Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) is a free Archicad add-on (MIT) that adds about
270 commands to the JSON API every Archicad serves on `http://127.0.0.1:19723`. Everything here goes through one
script next to this file, `scripts/tapir.py` (Python 3.8+, standard library only). Run it by its full path; below it
is written `tapir.py`.

The agent has to run on the computer Archicad runs on (Claude Code, Codex CLI or app, Cursor, ...); a cloud sandbox
cannot reach it.

## 1. Check the environment first

```
python tapir.py doctor
```

Use `python3` on macOS, `py -3` on Windows when `python` is missing. With no Python at all, ask the user to install
Python 3 (python.org, or `winget install Python.Python.3.13`). Every `[!!]` line says what to do:

- **No Archicad listens** - ask the user to start Archicad and open a project, or run `tapir.py open <project.pln>`.
  Archicad answers only once a project is open, not on its start dialog.
- **Tapir not loaded** - `tapir.py install` runs Tapir's official installer for every Archicad it finds
  (`--versions 28` for one, `--dryRun` to only show what it would do). For an Archicad under Program Files, Windows
  shows an administrator prompt: tell the user to click Yes. Archicad has to be restarted to load Tapir.
- **A newer Tapir is out** - Windows locks loaded add-ons: quit Archicad first (section 4), install, start it again.
- **Busy** - Archicad runs a long command or shows a dialog, or the user is mid-input. Wait, or ask the user to look.

Run `doctor` again until it prints `ready`. `install` and `help` need github.com (behind a proxy, set `HTTPS_PROXY`).

## 2. Run commands

```
tapir.py help                                   # every Tapir command of the version Archicad runs
tapir.py help GetDetailsOfElements              # one command: parameters, result, the definitions they use
tapir.py call GetProjectInfo
tapir.py call GetElementsByType elementType=Wall
tapir.py call GetDetailsOfElements @ids.json --out details.json
tapir.py call API.GetProductInfo                # Archicad's built-in commands
tapir.py call MyAddOn/RunCheck '{"level": 2}'   # any add-on's own JSON command: Namespace/Name
```

- Look the schema up with `help` before writing parameters. A wrong one fails with error 4002, and its message names
  the path that failed.
- Parameters: `key=value` pairs (values read as JSON when they parse), inline JSON, `@file.json`, or `-` for stdin.
  Windows PowerShell 5.1 strips the double quotes out of inline JSON and pipes non-ASCII text as `?`: there, use
  `key=value` or a UTF-8 `@file.json`.
- `--out file.json` writes the result as UTF-8 and prints only its outline (list lengths). Use it for anything large;
  PowerShell's `>` would write UTF-16.
- Elements are `{"elementId": {"guid": "..."}}`. Commands that take elements want `{"elements": [...]}` in exactly
  the form `GetElementsByType` and `GetSelectedElements` return them.
- More than a couple of calls: write a Python script. `call` returns the result and raises `RuntimeError` carrying
  Archicad's error. Send element lists in batches of a few hundred.

```python
import sys; sys.path.insert(0, r"<this skill>/scripts")
from tapir import call
walls = call("GetElementsByType", {"elementType": "Wall"})["elements"]
for i in range(0, len(walls), 300):
    for d in call("GetDetailsOfElements", {"elements": walls[i:i + 300]})["detailsOfElements"]:
        print(d["floorIndex"], d["layerIndex"])
```

## 3. Acceptance: trust only what you read back

`success: true` - for a whole command, or per element in `executionResults` - only means nothing failed, not that
anything changed. After every change, read the model again and compare it with what was expected:

- counts: `GetElementsByType` per type, split by story through the `floorIndex` of `GetDetailsOfElements`
- values: `GetDetailsOfElements` (geometry, layer, story; `fields` limits the output and the time it takes),
  `GetPropertyValuesOfElements`, `GetClassificationsOfElements`, `Get3DBoundingBoxes`
- what the user sees: `GetSelectedElements`, `GetCurrentWindowType`, `HighlightElements`

Report the numbers before and after and every difference, not a sample. When Archicad was reopened, first check that
the previous result is still there - a project that was not saved has lost it.

## 4. Developing an add-on: the rebuild loop

Archicad keeps every add-on it loaded locked, so building a new `.apx` (or updating Tapir) needs it closed:

```
tapir.py quit             # Archicad's own quit; prints the command that reopens the same project
<build the add-on>
tapir.py open "C:\Projects\test.pln" --exe "C:\Program Files\GRAPHISOFT\Archicad 28\Archicad.exe"
tapir.py call MyAddOn/RunCheck        # then check the model as in section 3
```

- `quit` asks about unsaved changes like the menu does and waits for the answer. Ask the user before quitting unless
  they told you to; never save their project on your own.
- One Archicad at a time: each further one takes the next port (19724, ...; `--port`) and a lot of memory. `open`
  refuses while another one runs; to switch projects inside a running Archicad, `call OpenProject projectFilePath=...`.
- When `open` keeps waiting, a dialog is waiting on screen: project recovery after a crash (Cancel keeps the recovery
  data for later), licence, missing libraries, file conversion. Tell the user.
- Give the add-on a JSON command for everything worth testing - a class deriving from `API_AddOnCommand`, registered
  in `Initialize()` with `ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler` (Archicad 27+;
  `ACAPI_Install_AddOnCommandHandler` before) - so the agent runs it with `call Namespace/Name` instead of a click.
  Have it answer with what its dialogs would have said instead of showing them.
- Debug output of a C++ add-on (`DBPrintf`, `OutputDebugString`; Windows): start `tapir.py dbwin --out dbwin.log` in
  the background **before** triggering the command, and read the file afterwards. Only one listener can run; it sees
  only lines sent after it started; it drops lines while Archicad is busy (compare a summary count with the detail
  lines); a debugger attached to Archicad takes the lines instead. Diagnostics that matter belong in a file the
  add-on writes.

## 5. Traps that fail silently

- **Hidden layers**: elements on a hidden layer cannot be selected, and `ChangeSelectionOfElements` still reports
  success. Show the layer first - `CreateLayers` with `overwriteExisting: true` changes an existing layer (there is no
  modify-layer command).
- **Selection**: when one `ChangeSelectionOfElements` call has an element in both lists,
  `removeElementsFromSelection` wins; send two calls. Removing thousands at once can freeze Archicad for many minutes:
  batch about 100, or ask the user to press Esc. Count with `GetSelectedElements` afterwards.
- **Window**: with the 3D window in front, deleting or selecting 2D elements (lines, texts, fills) reports success
  and does nothing. Check `GetCurrentWindowType`, then `ChangeWindow` to the floor plan.
- **Groups**: `DeleteElements` on one element of a group deletes the whole group; `MoveElements` on several members
  moves the group once per member. Look at `GetGroupsOfElements` first and act once per group. Suspend Groups does not
  change this.
- **One command at a time**: commands run on Archicad's main thread, so a long command or a modal dialog makes every
  other call time out. Error 4001 (invalid program status, ongoing user input) means the user is in the middle of
  something: ask them to finish it or press Esc. Don't point two agents at one Archicad.
- **Layer numbers**: element details give `layerIndex`; `GetAttributesByType` with `attributeType: Layer` gives each
  layer's `index`. Match on it, not on list order.
- **Imported drawings**: a merged DWG can be tens of thousands of elements. Moving or deleting them through Tapir is
  slow and gets refused mid-way when the user touches Archicad; ask the user to do it in Archicad.
- **Windows code page 65001** (the "Beta: Use Unicode UTF-8" option): Archicad refuses to start. `doctor` checks it.
- Before anything destructive, count; afterwards count again, and ask for Ctrl+Z at once if more changed than
  intended.
