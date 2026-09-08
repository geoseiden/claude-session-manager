# Claude Session Manager

A small desktop app for browsing and permanently deleting [Claude Code](https://claude.com/claude-code)
session transcripts. Claude Code keeps every conversation on disk forever, named
by UUID, with no built-in way to see what they are or remove the ones you don't
want. This shows them by their real chat names and lets you delete the ones you pick.

**Ships as a one-click app for Windows, macOS and Linux. Nothing to install** —
no Python, no runtime, no dependencies on the machine you run it on.

---

## Install

Grab the file for your machine from the [latest release](../../releases/latest) and open it.

| OS | Download | First launch |
|---|---|---|
| **Windows** | `ClaudeSessionManager-windows-x64.exe` | Double-click. Windows SmartScreen warns because the app isn't code-signed: click **More info → Run anyway**. |
| **macOS** (Apple Silicon) | `ClaudeSessionManager-macos-apple-silicon.zip` | Unzip, then **right-click the app → Open**. A plain double-click is refused for unsigned apps. |
| **macOS** (Intel) | `ClaudeSessionManager-macos-intel.zip` | Same as above. |
| **Linux** | `ClaudeSessionManager-linux-x86_64` | `chmod +x ClaudeSessionManager-linux-x86_64 && ./ClaudeSessionManager-linux-x86_64` |

The app opens your default browser to a page served from your own machine. Nothing
is uploaded anywhere — see [Privacy](#privacy). Quitting the app (Ctrl+C, or closing
its window) stops the server.

## Using it

**Browse…** opens your operating system's own folder chooser, so you can point the
app at any folder — including a `.claude` folder copied off another machine, a
backup drive, or a colleague's laptop. On startup it looks in the default location
and falls back to your home folder if Claude Code was never installed.

Each row shows:

- **Chat name** — the title Claude Code itself generated for the conversation
  (stored as an `ai-title` entry inside the transcript), with the opening prompt
  underneath. Sessions with no generated title are tagged `no title` and fall back
  to their first prompt.
- **Project** — the working directory the session ran in, shown as `~` for your
  home folder
- **Modified**, **Msgs**, **Size**

Click any column header to sort. Type in the search box to filter by name, project,
prompt text or session ID. Click a row to preview its opening prompts in the side
panel before deciding.

## Deleting

Tick the rows you want, press **Delete permanently**, and confirm in the dialog —
which lists exactly what will go and how much space it frees.

Deleting removes the session's `.jsonl` transcript **and** its sidecar folder of
subagent and workflow transcripts, so nothing is orphaned.

> **There is no undo.** Files are unlinked, not moved to a trash folder. If you want
> a safety net, copy your `~/.claude/projects` folder before a big cleanup.

Two guard rails are built in:

1. The server binds `127.0.0.1` only and requires a random token generated fresh on
   each run, so no other page open in your browser can drive it.
2. It refuses to delete any path that wasn't in the most recent scan.

## Where Claude Code stores sessions

| OS | Path |
|---|---|
| Linux / macOS | `~/.claude/projects/` |
| Windows | `%USERPROFILE%\.claude\projects\` |

Inside, one folder per project — named after the working directory with separators
replaced by dashes — containing one `.jsonl` file per session, named by UUID. Each
line is a JSON object: `user` and `assistant` messages, plus an `ai-title` entry
holding the generated chat name. A folder beside a transcript with the same UUID
holds that session's subagent and workflow transcripts.

Claude Code prunes transcripts on its own according to `cleanupPeriodDays` in
`~/.claude/settings.json` (30 days by default). This app is for the times you want
something gone now, or want to reclaim space without waiting.

## Running from source

Needs Python 3.8+ and nothing else.

```bash
python3 app.py                 # scan the default Claude projects folder
python3 app.py --root DIR      # start on a specific folder
python3 app.py --list          # plain terminal listing, no browser
python3 app.py --port 8791     # fixed port (default: any free one)
python3 app.py --no-browser    # start the server without opening a browser
```

macOS/Linux: `./run.sh` · Windows: `run.bat`

## Building the apps yourself

PyInstaller can't cross-compile, so each OS has to build its own binary.

**All four at once, without owning all three platforms** — push to GitHub and tag:

```bash
git tag v1.0.0 && git push --tags
```

[`.github/workflows/build.yml`](.github/workflows/build.yml) builds Windows, Linux,
Intel Mac and Apple Silicon Mac, then attaches all four to a GitHub Release. You can
also trigger it by hand from the **Actions** tab.

**Locally, for the OS you're on:**

```bash
pip install pyinstaller
python build.py       # output in dist/
```

## Troubleshooting

**"Could not open a folder picker on this system" (Linux)** — install one of the
standard dialog helpers:

```bash
sudo apt install zenity      # Debian/Ubuntu
sudo pacman -S zenity        # Arch
```

KDE users can install `kdialog` instead. Everything else in the app works without
them; you just lose the Browse button.

**Windows SmartScreen blocks the app** — expected for unsigned software. **More
info → Run anyway**. The only real fix is a code-signing certificate.

**macOS says the app is damaged or from an unidentified developer** — right-click
the app and choose **Open** rather than double-clicking. If it still refuses:

```bash
xattr -dr com.apple.quarantine "ClaudeSessionManager.app"
```

**Nothing opens when I run it** — the browser may have failed to launch. The app
prints its URL (like `http://127.0.0.1:53412/`); paste that into a browser. From a
double-clicked build with no console, run it from a terminal to see the URL, or use
`--port 8791` to know the address in advance.

**"Address already in use"** — an earlier copy is still running. Close it, or pass a
different `--port`.

**A session I know exists isn't listed** — files named `agent-*.jsonl` under
`subagents/` and `workflows/` are deliberately hidden. They belong to a parent
session and are deleted along with it.

**The list seems stale** — press **Rescan**. Results are cached by file size and
modification time in `~/.claude/.session-manager-cache.json`; delete that file to
force a full re-read.

## Privacy

Everything happens on your machine. The app makes no network requests, has no
analytics, and reads nothing outside the folder you point it at. The server listens
on `127.0.0.1` only, so it isn't reachable from your network.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The core rule: standard library only, one file.

## License

[MIT](LICENSE) — free for anyone to use, modify and distribute, including commercially.
