# archicad-tapir-skill

让 Claude Code、Codex 等编码 agent 通过 [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) 插件
连上正在运行的 Archicad，查询、修改、验收模型。[English below](#english)

- **检查环境**：Python、Windows 代码页、已装的 Archicad、正在运行的实例、Tapir 是否加载、是否最新版
- **自动安装 / 更新 Tapir**：调用 Tapir 官方安装器，下载与 Archicad 版本对应的最新版（需要管理员权限时，Windows 弹 UAC，macOS 弹密码框）
- **调用命令**：Tapir 的数百条命令、Archicad 自带的 `API.*` 命令、你自己插件注册的 JSON 命令；
  `help` 直接给出所装 Tapir 版本的命令清单（Archicad 不应答时为最新发布版）、参数和返回值 schema
- **开发验收**：退出 Archicad（不保存）→ 重新编译插件 → 带项目重启 → 触发插件命令 → 回读模型逐项核对；
  Windows 下抓 C++ 插件的 `OutputDebugString` 日志
- **静默失败清单**：隐藏图层选不中却报成功、3D 窗口在前时删除无效、删组内一个元素整组被删……

只用 Python 3.8+ 标准库，支持 Windows / macOS、Archicad 25 及以上。agent 必须跑在装 Archicad 的那台电脑上
（Claude Code、Codex CLI 或桌面版、Cursor 等）；网页聊天里的云端沙箱连不到本机 Archicad。
Archicad 的 JSON 端口没有密码，而且可能接受其他电脑的连接：请让防火墙阻止 Archicad 的入站连接
（Archicad 第一次启动时 Windows 若询问，不要允许）；本 skill 只用 127.0.0.1，不受影响。

## 安装

| Agent | 做法 |
|---|---|
| 任意（自动识别本机装了哪些 agent；需要 Node.js 22.20+ 和 git，在 PowerShell 里写 `npx.cmd`） | `npx skills add Garygaoxiang/archicad-tapir-skill -g` |
| Claude Code（需要 git） | 终端会话里输入 `/plugin marketplace add Garygaoxiang/archicad-tapir-skill`，再 `/plugin install archicad-tapir@archicad-tapir-skill`。桌面版会回复 `/plugin isn't available in this environment`：改用上面的 npx 或下面的手动方式 |
| Codex | 对 Codex 说：`$skill-installer install https://github.com/Garygaoxiang/archicad-tapir-skill/tree/main/skills/archicad-tapir` |
| 手动（不需要 git） | 在 GitHub 页面点 Code → Download ZIP，把解压出来的 `skills/archicad-tapir` 整个文件夹拷进 `~/.claude/skills/`（Claude Code；Windows 上是 `%USERPROFILE%\.claude\skills\`）或 `~/.agents/skills/`（Codex） |

没装 git：`winget install Git.Git`（或从 git-scm.com 下载），装完重开终端和 agent；也可以改用 Codex 或手动方式，这两种不需要 git。

装好后重开 agent，对它说「检查一下 Archicad 环境」或「连上 Archicad，数一下每层有几面墙」即可。
Codex 默认沙箱会断网，连 127.0.0.1 也连不上：让 agent 申请在沙箱外运行 `tapir.py`
（或在 `config.toml` 的 `[sandbox_workspace_write]` 里设 `network_access = true`）。

**更新**（装好的版本不会自己更新；更新后重开 agent）：

| 安装方式 | 更新 |
|---|---|
| npx | `npx skills update archicad-tapir -g` |
| Claude Code | `/plugin` → Installed → archicad-tapir → Update now，或在装了 Claude Code 命令行的终端里 `claude plugin update archicad-tapir@archicad-tapir-skill`；想自动更新：`/plugin` → Marketplaces → archicad-tapir-skill → Enable auto-update |
| Codex | 安装器遇到已存在的目录会中止：先删掉 `~/.codex/skills/archicad-tapir`，再说一次同样的安装命令 |
| 手动 | 删掉旧的 `archicad-tapir` 文件夹，重新拷一次 |

## 第一次用

agent 会先跑 `doctor`，每条 `[!!]` 都写明下一步：

```
[ok] Python 3.12.4 on Windows
[ok] Windows code page 936
[ok] latest Tapir release: 1.7.1
[--] Archicad 28 in C:\Program Files\GRAPHISOFT\Archicad 28: Tapir not installed (tapir.py install --versions 28)
[!!] port 19723: Archicad 28 CHI build 5000, Tapir not loaded (...): tapir.py install, then the user restarts Archicad (tapir.py quit needs Tapir loaded)
not ready - fix the [!!] lines, then run doctor again
```

照着做：`install` → 重启 Archicad → 再跑 `doctor`，直到输出 `ready`。Tapir 还没加载时 `tapir.py quit` 用不了：这一次由你自己保存、退出、再启动 Archicad。

## 脚本

`skills/archicad-tapir/scripts/tapir.py`，也可以不经 agent 直接用：

```
python tapir.py doctor                          检查环境
python tapir.py install [--versions 28]         安装 / 更新 Tapir（官方安装器，最新版）
python tapir.py help [命令名]                    列出命令，或看某条命令的参数和返回值
python tapir.py call GetElementsByType elementType=Wall
python tapir.py call MyAddOn/RunCheck "@params.json" --out result.json
python tapir.py quit                            退出 Archicad：不保存、不询问，未保存的修改会丢失（要保留先保存）
python tapir.py open 项目.pln                    带项目启动并等到它就绪（用 .pln 关联的 Archicad；指定版本加 --exe "C:\Program Files\GRAPHISOFT\Archicad 28\Archicad.exe"，写完整路径）
python tapir.py dbwin --out dbwin.log           Windows：抓插件的调试输出
```

在 Python 里：`from tapir import call`。不连 Archicad 的自检：`python tests/test_tapir.py`。

Tapir 由 [ENZYME-APD](https://github.com/ENZYME-APD) 及其贡献者开发，本仓库只是驱动它。MIT 许可证。

<a id="english"></a>
## English

A skill for coding agents (Claude Code, Codex, Cursor, ... - anything that reads `SKILL.md`) that connects them to a
running Archicad through the [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) add-on: it checks the
setup for first-time users, installs or updates Tapir with Tapir's official installer, runs Tapir, built-in and your
own add-ons' JSON commands (with each command's schema on hand through `help`), quits Archicad (without saving or
asking: unsaved changes are lost) and restarts it on a project for add-on rebuilds, captures a C++ add-on's debug
output on Windows, and lists the traps that fail silently. Python 3.8+ standard library only; Windows and macOS;
Archicad 25 or later. The agent has to run on the machine Archicad runs on (a cloud sandbox cannot reach it).
Archicad's JSON port has no password and can accept connections from other computers: keep the firewall blocking
incoming connections to Archicad (if Windows asks when Archicad first starts, do not allow it); the skill uses only
127.0.0.1, which the firewall does not filter.

Install: `npx skills add Garygaoxiang/archicad-tapir-skill -g` (any agent; needs Node.js 22.20+ and git, in
PowerShell type `npx.cmd`), or in a Claude Code terminal session (needs git; the desktop app answers that `/plugin`
is not available: use npx or the copy)
`/plugin marketplace add Garygaoxiang/archicad-tapir-skill` then `/plugin install archicad-tapir@archicad-tapir-skill`,
or in Codex `$skill-installer install https://github.com/Garygaoxiang/archicad-tapir-skill/tree/main/skills/archicad-tapir`,
or, with no git, use Codex (its installer downloads a ZIP) or take Code > Download ZIP on GitHub and copy its
`skills/archicad-tapir` into `~/.claude/skills/` (Claude Code; `%USERPROFILE%\.claude\skills\` on Windows) or
`~/.agents/skills/` (Codex). Get git with
`winget install Git.Git` or from git-scm.com, then restart the terminal and the agent. Installing
Tapir asks for administrator rights when the Add-Ons folder needs them (UAC on Windows, a password dialog on macOS).
Restart the agent, then ask it to check the Archicad setup: it runs `doctor` and follows each `[!!]` line until
doctor prints `ready` (the first restart of Archicad after installing Tapir is yours: `tapir.py quit` needs Tapir
loaded). The script also runs by hand
(`python tapir.py doctor | install | help | call | quit | open | dbwin`; `python tapir.py --help` lists them). Codex's default sandbox cuts the network, 127.0.0.1 included: let the agent run `tapir.py` outside the
sandbox (or set `network_access = true` under `[sandbox_workspace_write]` in `config.toml`).

Update (nothing updates by itself; restart the agent afterwards): `npx skills update archicad-tapir -g`; in a Claude
Code session `/plugin` > Installed > archicad-tapir > Update now, or, where the Claude Code command line is
installed, `claude plugin update archicad-tapir@archicad-tapir-skill`; to update automatically from then on,
`/plugin` > Marketplaces > archicad-tapir-skill > Enable auto-update (it takes effect from a later session start); in
Codex remove `~/.codex/skills/archicad-tapir` first, since the installer refuses an existing folder, then install
again; a manual copy is replaced by copying again.

Tapir is made by [ENZYME-APD](https://github.com/ENZYME-APD) and its contributors; this repository only drives it.
MIT licence.
