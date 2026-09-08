#!/usr/bin/env python3
"""Claude Session Manager - browse and permanently delete Claude Code session transcripts.

Cross-platform, Python standard library only. Serves a local UI to your browser.

    python3 app.py                  scan the default Claude projects folder
    python3 app.py --root DIR       scan a specific folder instead
    python3 app.py --list           plain terminal listing, no browser
    python3 app.py --no-browser     start the server but don't open a browser
"""

import argparse
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_NAME = "Claude Session Manager"
CACHE_PATH = Path.home() / ".claude" / ".session-manager-cache.json"
# Sidecar transcripts that belong to a parent session, not sessions in their own right.
SKIP_DIRS = {"subagents", "workflows", "shell-snapshots", "file-history"}


def log(*parts):
    """Windowed builds have no console; sys.stdout can be None there."""
    if sys.stdout is None:
        return
    try:
        print(*parts, flush=True)
    except (OSError, ValueError):
        pass


# ------------------------------------------------------------------ os dialogs


def pick_folder(start: Path):
    """Open the OS's own folder chooser. Returns a path string, or None if cancelled."""
    start = str(start if start.is_dir() else Path.home())
    if sys.platform == "darwin":
        cmd = ["osascript", "-e",
               'POSIX path of (choose folder with prompt "Select a folder to scan"'
               f' default location POSIX file "{start}")']
    elif os.name == "nt":
        cmd = ["powershell.exe", "-NoProfile", "-STA", "-Command",
               "Add-Type -AssemblyName System.Windows.Forms;"
               "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
               "$d.Description = 'Select a folder to scan';"
               f"$d.SelectedPath = '{start}';"
               "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"]
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory",
               "--title=Select a folder to scan", f"--filename={start}/"]
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", start]
    else:
        return None
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    picked = done.stdout.strip()
    return picked or None      # empty output = the user cancelled


def show_error(message: str):
    """Fatal-error popup for double-click launches with no console to print to."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display alert "{APP_NAME}" message "{message}"'], timeout=60)
        elif os.name == "nt":
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName System.Windows.Forms;"
                            f"[System.Windows.Forms.MessageBox]::Show('{message}',"
                            f"'{APP_NAME}')"], timeout=60)
        elif shutil.which("zenity"):
            subprocess.run(["zenity", "--error", f"--text={message}"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    log(message)


def default_root() -> Path:
    return Path.home() / ".claude" / "projects"


# --------------------------------------------------------------------------- scan


def find_session_files(root: Path):
    """Yield transcript files under root, skipping subagent/workflow sidecars."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".jsonl") and not name.startswith("agent-"):
                yield Path(dirpath) / name


def _text_of(content):
    """Message content is either a string or a list of typed blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _is_noise(text: str) -> bool:
    """System reminders, slash-command plumbing and tool output aren't real prompts."""
    t = text.strip()
    if not t:
        return True
    return t.startswith(("<", "Caveat:", "[Request interrupted", "API Error"))


def parse_session(path: Path) -> dict:
    """Read one transcript. Substring checks first so big files stay cheap."""
    title = ""
    first_prompt = ""
    prompts = []
    cwd = ""
    branch = ""
    users = 0
    assistants = 0
    first_ts = ""
    last_ts = ""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"ai-title"' in line:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                        title = obj["aiTitle"]          # last one wins = newest title
                    continue

                is_user = '"type":"user"' in line
                is_asst = '"type":"assistant"' in line
                if not (is_user or is_asst):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue

                kind = obj.get("type")
                if kind == "assistant":
                    assistants += 1
                elif kind == "user":
                    users += 1
                else:
                    continue

                if not cwd and obj.get("cwd"):
                    cwd = obj["cwd"]
                if not branch and obj.get("gitBranch"):
                    branch = obj["gitBranch"]
                ts = obj.get("timestamp") or ""
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts

                if kind == "user" and not obj.get("isMeta"):
                    text = _text_of((obj.get("message") or {}).get("content"))
                    if not _is_noise(text):
                        text = " ".join(text.split())
                        if not first_prompt:
                            first_prompt = text
                        if len(prompts) < 12:
                            prompts.append(text[:400])
    except OSError as exc:
        return {"error": str(exc)}

    stat = path.stat()
    return {
        "title": title or (first_prompt[:80] if first_prompt else "(untitled session)"),
        "has_ai_title": bool(title),
        "first_prompt": first_prompt,
        "prompts": prompts,
        "cwd": cwd,
        "branch": branch,
        "messages": users + assistants,
        "user_messages": users,
        "started": first_ts,
        "ended": last_ts,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def short_project(path: str) -> str:
    """A readable label: '~' for the home folder, otherwise the last path segment."""
    home = str(Path.home())
    if path == home:
        return "~"
    return Path(path).name or path


def decode_project_dir(name: str) -> str:
    """Claude encodes the working directory by replacing separators with dashes."""
    return "/" + name.lstrip("-").replace("-", "/") if name.startswith("-") else name


class Scanner:
    """Scans a folder, caching parse results by (path, size, mtime)."""

    def __init__(self):
        self.cache = self._load_cache()
        self.known_paths = set()

    @staticmethod
    def _load_cache() -> dict:
        try:
            return json.loads(CACHE_PATH.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self):
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(self.cache))
        except OSError:
            pass

    def scan(self, root: Path) -> list:
        sessions = []
        self.known_paths = set()
        for path in find_session_files(root):
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
            entry = self.cache.get(key)
            if not entry or entry.get("fp") != fingerprint:
                data = parse_session(path)
                if "error" in data:
                    continue
                entry = {"fp": fingerprint, "data": data}
                self.cache[key] = entry

            data = dict(entry["data"])
            sidecar = path.with_suffix("")
            if sidecar.is_dir():
                data["size"] += sum(f.stat().st_size
                                    for f in sidecar.rglob("*") if f.is_file())
            project = data.get("cwd") or decode_project_dir(path.parent.name)
            data.update(
                id=path.stem,
                path=key,
                project=project,
                project_short=short_project(project),
                has_sidecar=path.with_suffix("").is_dir(),
                modified=datetime.fromtimestamp(data["mtime"]).strftime("%Y-%m-%d %H:%M"),
            )
            sessions.append(data)
            self.known_paths.add(key)

        # Drop cache entries for files that no longer exist.
        for key in [k for k in self.cache if not Path(k).exists()]:
            del self.cache[key]
        self._save_cache()
        sessions.sort(key=lambda s: s["mtime"], reverse=True)
        return sessions

    def delete(self, paths):
        """Permanently remove transcripts (and their sidecar folders)."""
        removed, freed, errors = [], 0, []
        for raw in paths:
            path = Path(raw)
            if str(path) not in self.known_paths:
                errors.append(f"refused (not in last scan): {path.name}")
                continue
            try:
                freed += path.stat().st_size
                path.unlink()
                sidecar = path.with_suffix("")
                if sidecar.is_dir():
                    freed += sum(f.stat().st_size for f in sidecar.rglob("*") if f.is_file())
                    shutil.rmtree(sidecar)
                self.cache.pop(str(path), None)
                self.known_paths.discard(str(path))
                removed.append(path.name)
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        self._save_cache()
        return {"removed": len(removed), "freed": freed, "errors": errors}


# ----------------------------------------------------------------------------- ui

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Session Manager</title>
<style>
  :root{
    --bg:#f7f7f5; --panel:#fff; --line:#e3e2dd; --text:#1f1e1c; --muted:#6b6963;
    --accent:#c15f3c; --accent-soft:#f6ece7; --danger:#b3261e; --danger-soft:#fbeceb;
    --row-hover:#f2f1ee; --row-sel:#f6ece7;
  }
  @media (prefers-color-scheme:dark){:root{
    --bg:#1b1a18; --panel:#232220; --line:#38352f; --text:#eceae5; --muted:#9d9890;
    --accent:#e08360; --accent-soft:#33261f; --danger:#f2836f; --danger-soft:#3a221e;
    --row-hover:#2b2926; --row-sel:#33261f;
  }}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  header{position:sticky;top:0;z-index:5;background:var(--panel);
         border-bottom:1px solid var(--line);padding:12px 16px}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  h1{font-size:15px;font-weight:600;margin:0 12px 0 0;letter-spacing:-.01em}
  input[type=text],input[type=search]{background:var(--bg);color:var(--text);
    border:1px solid var(--line);border-radius:7px;padding:7px 10px;font:inherit;min-width:0}
  #root{flex:1;min-width:220px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
        font-size:12.5px;cursor:pointer}
  #root:hover{border-color:var(--accent)}
  #q{width:230px}
  button{background:var(--panel);color:var(--text);border:1px solid var(--line);
    border-radius:7px;padding:7px 12px;font:inherit;cursor:pointer}
  button:hover:not(:disabled){background:var(--row-hover)}
  button:disabled{opacity:.45;cursor:default}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  button.danger{background:var(--danger);border-color:var(--danger);color:#fff}
  .stats{color:var(--muted);font-size:12.5px;margin-left:auto;white-space:nowrap}
  main{display:flex;height:calc(100vh - 62px)}
  .list{flex:1;overflow:auto}
  table{width:100%;border-collapse:collapse}
  th{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
     text-align:left;font-size:12px;font-weight:600;color:var(--muted);
     padding:8px 10px;cursor:pointer;user-select:none;white-space:nowrap}
  th.nosort{cursor:default}
  td{border-bottom:1px solid var(--line);padding:8px 10px;vertical-align:top}
  tr:hover td{background:var(--row-hover)}
  tr.sel td{background:var(--row-sel)}
  .name{font-weight:500}
  .sub{color:var(--muted);font-size:12px;margin-top:2px;
       overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46vw}
  .meta{color:var(--muted);font-size:12.5px;white-space:nowrap}
  .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .tag{display:inline-block;background:var(--accent-soft);color:var(--accent);
       border-radius:4px;padding:1px 5px;font-size:11px;margin-left:6px;vertical-align:1px}
  aside{width:380px;border-left:1px solid var(--line);background:var(--panel);
        overflow:auto;padding:16px;display:none}
  aside.open{display:block}
  aside h2{font-size:14px;margin:0 0 4px}
  aside .path{font-family:ui-monospace,monospace;font-size:11.5px;color:var(--muted);
              word-break:break-all;margin-bottom:12px}
  .msg{border-left:2px solid var(--line);padding:2px 0 2px 10px;margin-bottom:10px;
       font-size:13px;color:var(--text);white-space:pre-wrap;word-break:break-word}
  .empty{padding:40px 16px;color:var(--muted);text-align:center}
  dialog{border:1px solid var(--line);border-radius:12px;background:var(--panel);
         color:var(--text);padding:20px;max-width:460px;box-shadow:0 12px 40px #0003}
  dialog::backdrop{background:#0006}
  dialog h3{margin:0 0 8px;font-size:15px}
  dialog ul{max-height:180px;overflow:auto;margin:10px 0;padding-left:18px;
            font-size:13px;color:var(--muted)}
  .warn{background:var(--danger-soft);color:var(--danger);border-radius:7px;
        padding:8px 10px;font-size:12.5px;margin:10px 0}
</style></head><body>
<header>
  <div class="row">
    <h1>Claude Session Manager</h1>
    <input type="text" id="root" spellcheck="false" readonly>
    <button id="browse" class="primary">Browse&hellip;</button>
    <button id="rescan">Rescan</button>
    <input type="search" id="q" placeholder="Filter by name, project, prompt...">
    <span class="stats" id="stats">Loading...</span>
  </div>
  <div class="row" style="margin-top:10px">
    <label class="meta"><input type="checkbox" id="all"> Select all shown</label>
    <button id="del" class="danger" disabled>Delete permanently</button>
    <span class="meta" id="selinfo"></span>
  </div>
</header>
<main>
  <div class="list">
    <table>
      <thead><tr>
        <th class="nosort" style="width:28px"></th>
        <th data-k="title">Chat name</th>
        <th data-k="project_short">Project</th>
        <th data-k="mtime">Modified</th>
        <th data-k="messages" class="num">Msgs</th>
        <th data-k="size" class="num">Size</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="empty" id="empty" hidden>No sessions found here.</div>
  </div>
  <aside id="side"></aside>
</main>
<dialog id="confirm">
  <h3>Delete permanently?</h3>
  <div id="cbody"></div>
  <div class="warn">This cannot be undone. Files are removed from disk, not moved to a trash folder.</div>
  <div class="row" style="justify-content:flex-end">
    <button id="cancel">Cancel</button>
    <button id="ok" class="danger">Delete</button>
  </div>
</dialog>
<script>
const TOKEN = "__TOKEN__";
let sessions = [], sel = new Set(), sortKey = "mtime", sortDir = -1;
const $ = id => document.getElementById(id);
const fmtSize = n => n < 1024 ? n + " B"
  : n < 1048576 ? (n/1024).toFixed(0) + " KB"
  : (n/1048576).toFixed(1) + " MB";

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: {"X-Token": TOKEN, "Content-Type": "application/json", ...(opts.headers||{})}
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function load(root) {
  $("stats").textContent = "Scanning...";
  const data = await api("/api/sessions?root=" + encodeURIComponent(root || ""));
  sessions = data.sessions;
  $("root").value = data.root;
  sel.clear();
  render();
}

function shown() {
  const q = $("q").value.toLowerCase().trim();
  if (!q) return sessions;
  return sessions.filter(s =>
    (s.title + " " + s.project + " " + s.first_prompt + " " + s.id).toLowerCase().includes(q));
}

function render() {
  const list = shown().slice().sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * sortDir;
  });
  $("rows").innerHTML = list.map(s => `
    <tr data-p="${encodeURIComponent(s.path)}" class="${sel.has(s.path) ? "sel" : ""}">
      <td><input type="checkbox" ${sel.has(s.path) ? "checked" : ""}></td>
      <td><div class="name">${esc(s.title)}${s.has_ai_title ? "" : '<span class="tag">no title</span>'}</div>
          <div class="sub">${esc(s.first_prompt || "")}</div></td>
      <td class="meta">${esc(s.project_short)}</td>
      <td class="meta">${s.modified}</td>
      <td class="num meta">${s.messages}</td>
      <td class="num meta">${fmtSize(s.size)}</td>
    </tr>`).join("");
  $("empty").hidden = list.length > 0;
  const total = sessions.reduce((n, s) => n + s.size, 0);
  $("stats").textContent = `${list.length} of ${sessions.length} sessions · ${fmtSize(total)} total`;
  const selSize = sessions.filter(s => sel.has(s.path)).reduce((n, s) => n + s.size, 0);
  $("selinfo").textContent = sel.size ? `${sel.size} selected · ${fmtSize(selSize)}` : "";
  $("del").disabled = sel.size === 0;
  $("all").checked = list.length > 0 && list.every(s => sel.has(s.path));
}

const esc = s => (s || "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

$("rows").addEventListener("click", e => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  const path = decodeURIComponent(tr.dataset.p);
  if (e.target.type === "checkbox") {
    sel.has(path) ? sel.delete(path) : sel.add(path);
    render();
  } else {
    preview(sessions.find(s => s.path === path));
  }
});

function preview(s) {
  if (!s) return;
  $("side").className = "open";
  $("side").innerHTML = `
    <h2>${esc(s.title)}</h2>
    <div class="path">${esc(s.project)}${s.branch ? " · " + esc(s.branch) : ""}<br>${esc(s.path)}</div>
    <div class="meta" style="margin-bottom:14px">
      ${s.messages} messages · ${fmtSize(s.size)} · ${s.modified}
      ${s.has_sidecar ? "<br>has a sidecar folder (subagents/workflows) - deleted with it" : ""}
    </div>
    ${s.prompts.map(p => `<div class="msg">${esc(p)}</div>`).join("") || '<div class="meta">No readable prompts.</div>'}`;
}

$("q").addEventListener("input", render);
$("rescan").addEventListener("click", () => load($("root").value));

async function browse() {
  $("browse").disabled = true;
  $("stats").textContent = "Waiting for the folder picker\u2026";
  try {
    const {path} = await api("/api/pick");
    if (path) await load(path); else render();
  } catch (err) {
    alert("Could not open a folder picker on this system.\n\n" + err.message);
    render();
  } finally {
    $("browse").disabled = false;
  }
}
$("browse").addEventListener("click", browse);
$("root").addEventListener("click", browse);
$("all").addEventListener("change", e => {
  shown().forEach(s => e.target.checked ? sel.add(s.path) : sel.delete(s.path));
  render();
});
document.querySelectorAll("th[data-k]").forEach(th => th.addEventListener("click", () => {
  const k = th.dataset.k;
  sortDir = sortKey === k ? -sortDir : (k === "mtime" || k === "size" || k === "messages" ? -1 : 1);
  sortKey = k;
  render();
}));

$("del").addEventListener("click", () => {
  const picked = sessions.filter(s => sel.has(s.path));
  const bytes = picked.reduce((n, s) => n + s.size, 0);
  $("cbody").innerHTML =
    `<div>Deleting <b>${picked.length}</b> session${picked.length > 1 ? "s" : ""}, freeing ${fmtSize(bytes)}:</div>
     <ul>${picked.map(s => `<li>${esc(s.title)}</li>`).join("")}</ul>`;
  $("confirm").showModal();
});
$("cancel").addEventListener("click", () => $("confirm").close());
$("ok").addEventListener("click", async () => {
  $("confirm").close();
  const res = await api("/api/delete", {method: "POST", body: JSON.stringify({paths: [...sel]})});
  if (res.errors.length) alert("Some deletions failed:\n" + res.errors.join("\n"));
  $("side").className = "";
  await load($("root").value);
});

load("");
</script></body></html>'''


# ------------------------------------------------------------------------- server


def make_handler(scanner: Scanner, token: str, state: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep the terminal clean

        def _send(self, code, body, ctype="application/json"):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authed(self) -> bool:
            if self.headers.get("X-Token") == token:
                return True
            self._send(403, json.dumps({"error": "bad token"}))
            return False

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, PAGE.replace("__TOKEN__", token), "text/html")
            if url.path == "/api/sessions":
                if not self._authed():
                    return
                arg = (parse_qs(url.query).get("root") or [""])[0].strip()
                root = Path(arg).expanduser() if arg else state["root"]
                if not root.is_dir():
                    return self._send(400, json.dumps({"error": f"not a folder: {root}"}))
                state["root"] = root
                return self._send(200, json.dumps(
                    {"root": str(root), "sessions": scanner.scan(root)}))
            if url.path == "/api/pick":
                if not self._authed():
                    return
                picked = pick_folder(state["root"])
                return self._send(200, json.dumps({"path": picked or ""}))
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if urlparse(self.path).path != "/api/delete":
                return self._send(404, json.dumps({"error": "not found"}))
            if not self._authed():
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                paths = json.loads(self.rfile.read(length) or b"{}").get("paths", [])
            except ValueError:
                return self._send(400, json.dumps({"error": "bad json"}))
            result = scanner.delete(paths)
            log(f"  deleted {result['removed']} session(s), "
                f"freed {result['freed'] / 1048576:.1f} MB")
            self._send(200, json.dumps(result))

    return Handler


def run_gui(root: Path, port: int, open_browser: bool):
    scanner = Scanner()
    token = secrets.token_urlsafe(24)
    state = {"root": root}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(scanner, token, state))
    url = f"http://127.0.0.1:{server.server_port}/"
    log(f"{APP_NAME}\n  scanning: {root}\n  open:     {url}\n  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\nbye")


def run_list(root: Path):
    sessions = Scanner().scan(root)
    total = 0
    for s in sessions:
        total += s["size"]
        print(f"{s['modified']}  {s['size']/1024:7.0f}K  {s['project_short'][:22]:22}  {s['title'][:60]}")
        print(f"{'':21}{s['path']}")
    print(f"\n{len(sessions)} sessions, {total/1048576:.1f} MB in {root}")


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--root", help="folder to scan (default: ~/.claude/projects)")
    ap.add_argument("--port", type=int, default=0, help="port (default: any free port)")
    ap.add_argument("--list", action="store_true", help="print a terminal listing and exit")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser")
    args = ap.parse_args()

    root = Path(args.root).expanduser() if args.root else default_root()
    if not root.is_dir():
        # First run on a machine that has never used Claude Code: start at home
        # and let the folder picker take over rather than dying with an error.
        log(f"no Claude projects folder at {root}, starting at your home folder")
        root = Path.home()

    try:
        if args.list:
            run_list(root)
        else:
            run_gui(root, args.port, not args.no_browser)
    except OSError as exc:
        show_error(f"{APP_NAME} could not start: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
