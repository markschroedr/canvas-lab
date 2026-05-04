#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from base64 import b64decode
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "projects"
HOST = "127.0.0.1"
PORT = int(os.environ.get("DESIGN_BRIDGE_PORT", "8787"))
CODEX_MODEL = os.environ.get("DESIGN_CODEX_MODEL", "gpt-5.5")
CLAUDE_MODEL = os.environ.get("DESIGN_CLAUDE_MODEL", "claude-opus-4-7")
CLAUDE_EFFORT = os.environ.get("DESIGN_CLAUDE_EFFORT", "high")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str | None, fallback: str = "default") -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", value or fallback).strip(".-")
    return safe or fallback


def project_dir(project: str | None) -> Path:
    name = safe_name(project)
    path = (PROJECTS_DIR / name).resolve()
    if PROJECTS_DIR.resolve() not in path.parents and path != PROJECTS_DIR.resolve():
        raise ValueError("invalid project")
    return path


def chat_path(project: str | None, chat: str | None) -> Path:
    return project_dir(project) / "chats" / f"{safe_name(chat)}.jsonl"


def attachments_dir(project: str | None) -> Path:
    return project_dir(project) / "attachments"


def status_path(project: str | None, chat: str | None) -> Path:
    return project_dir(project) / "chats" / f"{safe_name(chat)}.status.json"


def qa_path(project: str | None, chat: str | None) -> Path:
    return project_dir(project) / "chats" / f"{safe_name(chat)}.qa.json"


def project_jsx_files(project: str | None) -> list[str]:
    path = project_dir(project)
    component_dir = path / "components"
    files: list[str] = []
    if component_dir.exists():
        files.extend(f"components/{item.name}" for item in sorted(component_dir.glob("*.jsx")) if item.is_file())
    files.append("canvas.jsx")
    return files


def project_version(project: str | None) -> str:
    path = project_dir(project)
    parts = []
    for file in project_jsx_files(project):
        item = path / file
        if item.exists():
            stat = item.stat()
            parts.append(f"{file}:{stat.st_mtime_ns}:{stat.st_size}")
        else:
            parts.append(f"{file}:missing")
    return "|".join(parts)


def script_text(value: str) -> str:
    return value.replace("</script", "<\\/script")


def build_export_html(project: str | None, mode: str = "download") -> str:
    project_name = safe_name(project)
    path = project_dir(project_name)
    files = project_jsx_files(project_name)
    scripts = []
    for file in files:
        source_path = path / file
        if not source_path.exists():
            continue
        source = script_text(source_path.read_text(encoding="utf-8"))
        scripts.append(f'<script type="text/babel" data-file="{file}">\n(() => {{\n{source}\n}})();\n</script>')

    print_class = "export-mode-print" if mode == "print" else "export-mode-canvas"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{project_name} · Design Canvas Export</title>
  <link rel="icon" href="data:,">
  <script src="https://unpkg.com/react@18.3.1/umd/react.development.js" crossorigin="anonymous"></script>
  <script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" crossorigin="anonymous"></script>
  <script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>
  <style>
    :root {{
      --page: oklch(98% 0.003 250);
      --surface: oklch(100% 0 0);
      --ink: oklch(22% 0.012 250);
      --muted: oklch(50% 0.012 250);
      --line: oklch(91% 0.006 250);
      --canvas-dot: oklch(88% 0.012 250);
      --shadow: 0 1px 2px oklch(20% 0.02 250 / 0.04), 0 18px 44px oklch(20% 0.02 250 / 0.1);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color-scheme: light;
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle, var(--canvas-dot) 1px, transparent 1px) 0 0 / 24px 24px,
        var(--page);
      color: var(--ink);
      -webkit-font-smoothing: antialiased;
      text-wrap: pretty;
    }}

    button, input, textarea, select {{ font: inherit; }}
    .export-shell {{ min-height: 100vh; }}
    .export-toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 56px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: color-mix(in oklch, var(--surface), transparent 4%);
      backdrop-filter: blur(16px);
    }}
    .export-title {{ display: grid; gap: 2px; min-width: 0; }}
    .export-title strong {{ font-size: 13px; font-weight: 650; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .export-title span {{ color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .export-action {{
      min-height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--ink);
      padding: 0 12px;
      cursor: pointer;
    }}

    .canvas-viewport {{
      position: relative;
      min-height: calc(100vh - 56px);
      overflow: auto;
      padding: 96px 96px 160px;
    }}
    .canvas-world {{
      position: relative;
      width: 2400px;
      height: 1500px;
      transform-origin: 0 0;
    }}
    .page-viewport {{
      min-height: calc(100vh - 56px);
      background: var(--page);
    }}
    .page-shell {{
      width: 100%;
      min-height: calc(100vh - 56px);
      margin: 0 auto;
      background: var(--surface);
    }}
    .dc-page {{ min-width: 0; min-height: 100%; }}
    .dc-section {{ position: absolute; display: grid; gap: 18px; }}
    .dc-section-heading {{ display: grid; gap: 3px; color: var(--muted); font-size: 12px; user-select: none; }}
    .dc-section-heading strong {{ color: var(--ink); font-size: 14px; }}
    .dc-section-artboards {{ position: relative; }}
    .artboard-frame {{ position: absolute; display: grid; gap: 8px; }}
    .artboard-meta {{ display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; user-select: none; }}
    .artboard {{
      position: relative;
      display: grid;
      grid-template-rows: minmax(0, 1fr);
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--surface);
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .artboard > * {{ min-width: 0; max-width: 100%; min-height: 0; height: 100%; }}
    .dc-screen {{
      width: 100%;
      height: 100%;
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      gap: 16px;
      overflow: hidden;
    }}
    .dc-screen-body {{
      min-width: 0;
      min-height: 0;
      display: grid;
      align-content: start;
      gap: 14px;
      overflow: auto;
    }}
    .screen-kicker {{ color: var(--muted); font-size: 12px; }}
    .screen-heading {{ margin: 0; max-width: 15ch; font-size: 26px; line-height: 1.05; letter-spacing: 0; }}
    .screen-copy {{ margin: 0; color: var(--muted); font-size: 14px; line-height: 1.45; }}

    .export-mode-print {{
      background: var(--surface);
    }}
    .export-mode-print .canvas-viewport {{
      min-height: auto;
      overflow: visible;
      padding: 32px;
      background: var(--surface);
    }}
    .export-mode-print .canvas-world {{
      width: auto;
      height: auto;
      display: grid;
      gap: 40px;
    }}
    .export-mode-print .dc-section,
    .export-mode-print .artboard-frame {{
      position: static !important;
    }}
    .export-mode-print .dc-section {{
      display: grid;
      gap: 16px;
    }}
    .export-mode-print .dc-section-artboards {{
      position: static;
      display: grid;
      gap: 28px;
    }}
    .export-mode-print .artboard-frame {{
      width: min(100%, var(--export-artboard-width, 100%)) !important;
      max-width: 100%;
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    .export-mode-print .artboard {{
      height: auto !important;
      min-height: 0;
      display: block;
      overflow: visible;
      padding: 0;
      border: 0;
      border-radius: 0;
      box-shadow: none;
    }}
    .export-mode-print .artboard > *,
    .export-mode-print .dc-screen,
    .export-mode-print .dc-screen-body {{
      height: auto !important;
      min-height: 0 !important;
      max-height: none !important;
      overflow: visible !important;
    }}
    .export-mode-print .dc-screen {{
      display: block;
    }}
    .export-mode-print .dc-screen-body {{
      display: block;
    }}
    .export-mode-print .artboard [style*="height"] {{
      max-height: none !important;
    }}

    @media print {{
      @page {{ margin: 12mm; }}
      body {{ background: white; }}
      .export-toolbar {{ display: none; }}
      .canvas-viewport {{ min-height: auto; padding: 0; overflow: visible; }}
      .canvas-world {{ transform: none; width: auto; height: auto; }}
      .page-viewport, .page-shell {{ min-height: auto; }}
      .artboard-frame {{ break-after: page; page-break-after: always; }}
      .artboard-frame:last-child {{ break-after: auto; page-break-after: auto; }}
    }}
  </style>
</head>
<body class="{print_class}">
  <div id="export-root"></div>
  {"".join(scripts)}
  <script type="text/babel">
    const ARTBOARD = {{
      mobile: {{ width: 390, height: 844 }},
      mobileShort: {{ width: 390, height: 720 }},
      desktop: {{ width: 1280, height: 820 }},
      desktopWide: {{ width: 1440, height: 900 }}
    }};

    function DCSection({{ id, title, note, x, y, children }}) {{
      return (
        <section className="dc-section" style={{{{ left: x, top: y }}}} data-agent-id={{`${{id}}.section`}}>
          <header className="dc-section-heading" data-agent-id={{`${{id}}.heading`}}>
            <strong>{{title}}</strong>
            {{note && <span>{{note}}</span>}}
          </header>
          <div className="dc-section-artboards" data-agent-id={{`${{id}}.artboards`}}>{{children}}</div>
        </section>
      );
    }}

    function DCArtboard({{ id, label, note, x, y, width = 390, height = 640, tone = 184, children }}) {{
      const style = {{
        left: x,
        top: y,
        width,
        "--export-artboard-width": `${{width}}px`,
        "--dc-artboard-height": `${{height}}px`,
        "--accent": `oklch(58% 0.13 ${{tone}})`,
        "--accent-soft": `oklch(91% 0.055 ${{tone}})`
      }};
      return (
        <article className="artboard-frame" style={{style}} data-agent-id={{`${{id}}.frame`}}>
          <div className="artboard-meta" data-agent-id={{`${{id}}.meta`}}>
            <strong>{{label}}</strong>
            {{note && <span>{{note}}</span>}}
          </div>
          <section className="artboard" style={{{{ height: "var(--dc-artboard-height)" }}}} data-agent-id={{`${{id}}.artboard`}}>
            {{children}}
          </section>
        </article>
      );
    }}

    function DCPage({{ id, children }}) {{
      return <main className="dc-page" data-agent-id={{`${{id}}.page`}}>{{children}}</main>;
    }}

    function ExportApp() {{
      const project = window.DesignProject;
      if (!project?.render) return <main className="canvas-viewport">Project did not define window.DesignProject.render.</main>;
      const isPage = project.view === "page";
      return (
        <div className="export-shell">
          <header className="export-toolbar">
            <div className="export-title">
              <strong>{{project.title || "{project_name}"}}</strong>
              <span>{{project.subtitle || "Design Canvas export"}}</span>
            </div>
            <button className="export-action" onClick={{() => window.print()}}>Print / PDF</button>
          </header>
          {{isPage ? (
            <section className="page-viewport">
              <div className="page-shell">
                {{project.render({{ DCSection, DCArtboard, DCPage, ARTBOARD }})}}
              </div>
            </section>
          ) : (
            <section className="canvas-viewport">
              <div className="canvas-world">
                {{project.render({{ DCSection, DCArtboard, DCPage, ARTBOARD }})}}
              </div>
            </section>
          )}}
        </div>
      );
    }}

    ReactDOM.createRoot(document.getElementById("export-root")).render(<ExportApp />);
  </script>
</body>
</html>
"""


def read_json(request) -> dict:
    length = int(request.headers.get("content-length", "0"))
    raw = request.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def read_history(project: str | None, chat: str | None) -> list[dict]:
    path = chat_path(project, chat)
    if not path.exists():
        return []

    messages: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            messages.append(json.loads(line))
    return messages


def append_history(project: str | None, chat: str | None, message: dict) -> None:
    path = chat_path(project, chat)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")


def read_status(project: str | None, chat: str | None) -> dict:
    path = status_path(project, chat)
    if not path.exists():
        return {"state": "idle"}
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(project: str | None, chat: str | None, status: dict) -> None:
    path = status_path(project, chat)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")


def read_qa(project: str | None, chat: str | None) -> dict | None:
    path = qa_path(project, chat)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_qa(project: str | None, chat: str | None, qa: dict) -> None:
    path = qa_path(project, chat)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(qa, ensure_ascii=False), encoding="utf-8")


def git_run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False)


def git_has_head(cwd: Path) -> bool:
    return git_run(cwd, ["rev-parse", "--verify", "HEAD"]).returncode == 0


def git_has_staged_changes(cwd: Path) -> bool:
    return git_run(cwd, ["diff", "--cached", "--quiet"]).returncode == 1


def git_commit_hash(cwd: Path) -> str | None:
    result = git_run(cwd, ["rev-parse", "--short", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ensure_project_git(cwd: Path) -> dict:
    info: dict = {"ok": True, "initialized": False, "baseline": None}
    if not (cwd / ".git").exists():
        result = git_run(cwd, ["init"])
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or result.stdout).strip()}
        info["initialized"] = True

    ignore_path = cwd / ".gitignore"
    ignore_lines = ["chats/", "attachments/", "qa/", ".DS_Store"]
    existing = ignore_path.read_text(encoding="utf-8").splitlines() if ignore_path.exists() else []
    additions = [line for line in ignore_lines if line not in existing]
    if additions:
        with ignore_path.open("a", encoding="utf-8") as handle:
            if existing and existing[-1].strip():
                handle.write("\n")
            handle.write("\n".join(additions) + "\n")

    git_run(cwd, ["config", "user.name", "Canvas Lab"])
    git_run(cwd, ["config", "user.email", "canvas-lab@local"])

    if not git_has_head(cwd):
        git_run(cwd, ["add", "-A"])
        if git_has_staged_changes(cwd):
            commit = git_run(cwd, ["commit", "-m", "Initial project snapshot"])
            if commit.returncode == 0:
                info["baseline"] = git_commit_hash(cwd)
            else:
                return {"ok": False, "error": (commit.stderr or commit.stdout).strip()}

    return info


def summarize_commit_message(agent: str, message: str) -> str:
    summary = "update canvas"
    for line in (message or "").splitlines():
        line = line.strip()
        if line:
            summary = line
            break
    summary = re.sub(r"\s+", " ", summary)
    if len(summary) > 72:
        summary = summary[:69].rstrip() + "..."
    return f"{agent}: {summary}"


def commit_project_changes(cwd: Path, agent: str, message: str) -> dict:
    try:
        setup = ensure_project_git(cwd)
        if not setup.get("ok"):
            return {"ok": False, "error": setup.get("error") or "could not initialize project git"}

        git_run(cwd, ["add", "-A"])
        if not git_has_staged_changes(cwd):
            return {"ok": True, "changed": False, "message": "No project file changes to commit.", "baseline": setup.get("baseline")}

        commit = git_run(cwd, ["commit", "-m", summarize_commit_message(agent, message)])
        if commit.returncode != 0:
            return {"ok": False, "error": (commit.stderr or commit.stdout).strip(), "baseline": setup.get("baseline")}

        return {
            "ok": True,
            "changed": True,
            "commit": git_commit_hash(cwd),
            "baseline": setup.get("baseline"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def combined_output(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(stdout.strip())
    if stderr.strip():
        parts.append(stderr.strip())
    return "\n\n".join(parts)


def chat_content(agent: str, exit_code: int, stdout: str, stderr: str) -> str:
    if exit_code == 0 and stdout.strip():
        return stdout.strip()
    return combined_output(stdout, stderr)


def attachment_lines(attachments: list[dict] | None) -> list[str]:
    lines = []
    for item in attachments or []:
        path = item.get("path")
        if not path:
            continue
        name = item.get("name") or Path(path).name
        mime = item.get("mime") or "image"
        lines.append(f"- {path} ({mime}, {name})")
    return lines


def save_attachment(payload: dict) -> dict:
    project = payload.get("project") or "default"
    mime = str(payload.get("mime") or "")
    if not mime.startswith("image/"):
        raise ValueError("only image attachments are supported")

    raw_data = str(payload.get("data") or "")
    if "," in raw_data and raw_data.startswith("data:"):
        raw_data = raw_data.split(",", 1)[1]

    data = b64decode(raw_data, validate=True)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("image attachment is larger than 10 MB")

    ext_by_mime = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = ext_by_mime.get(mime, ".png")
    name = safe_name(Path(str(payload.get("name") or "image")).stem, "image")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    filename = f"{stamp}-{name}{ext}"
    directory = attachments_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(data)

    project_name = safe_name(project)
    relative_path = f"attachments/{filename}"
    return {
        "name": payload.get("name") or filename,
        "mime": mime,
        "path": relative_path,
        "url": f"/projects/{project_name}/{relative_path}",
        "size": len(data),
    }


def build_prompt(payload: dict, history: list[dict], cwd: Path) -> str:
    file_path = payload.get("file") or "canvas.jsx"
    selector = payload.get("selector") or "(none)"
    message = payload.get("message") or ""
    project = payload.get("project") or cwd.name
    chat = payload.get("chat") or "default"
    qa = read_qa(project, chat)
    qa_summary = qa.get("summary") if qa else ""
    current_attachments = attachment_lines(payload.get("attachments"))
    selected_element_context = (
        f'The user has marked this element and is referring to it: `{selector}`.'
        if selector != "(none)"
        else "The user has not marked a specific element."
    )

    history_lines = []
    for item in history:
        role = item.get("role", "unknown")
        content = item.get("content", "")
        item_selector = item.get("selector")
        prefix = f"{role}:"
        if item_selector:
            prefix += f" selector={item_selector}"
        lines = [f"{prefix}\n{content}"]
        item_attachment_lines = attachment_lines(item.get("attachments"))
        if item_attachment_lines:
            lines.append("Attached images:\n" + "\n".join(item_attachment_lines))
        history_lines.append("\n".join(lines))

    return f"""Read AGENTS.md and DESIGN.md in the current project folder.

You are editing one project inside a canvas-based design workspace.
The canvas renderer is central and shared. Project-specific files live in this current folder.
Edit only this project folder unless the user explicitly asks to change shared infrastructure.

Current project folder:
{cwd}

Current file:
{file_path}

Current marked element:
{selected_element_context}

Attached images for the current user request:
{chr(10).join(current_attachments) if current_attachments else "(none)"}

Latest automatic canvas QA:
{qa_summary or "(none yet)"}

Full chat history:
{chr(10).join(history_lines) if history_lines else "(empty)"}

Current user request:
{message}

Rules:
- Edit files directly in this project folder when the request asks for a change.
- If a current marked element is present, treat it as the user's target unless the request clearly says otherwise.
- If attached images are present, inspect/use those local image files as reference material for the user request.
- Keep `canvas.jsx` focused on the board composition: `DCSection`, `DCArtboard`, and project metadata.
- Use `components/*.jsx` for reusable or larger screens, especially when creating multiple screens or flows.
- Component files should assign exported components to `window`, for example: `window.LoginScreen = LoginScreen`.
- Use `window.DesignProject.view = "page"` with `DCPage` for one fullscreen page or app view.
- Use the default canvas view with `DCSection` and `DCArtboard` for variants, flows, comparisons, or multiple screens.
- Treat each `DCArtboard` width/height as a fixed viewport.
- Use the renderer presets when possible: `ARTBOARD.mobile` is 390x844, `ARTBOARD.desktop` is 1280x820.
- Prefer one root per artboard with `className="dc-screen"` and an inner flexible `className="dc-screen-body"` when needed.
- Do not leave accidental empty whitespace in an artboard; compose the full viewport intentionally.
- Do not allow horizontal overflow. Use `min-width: 0`, `max-width: 100%`, wrapping, density changes, or an intentional inner scroll area.
- Keep project files lightweight.
- Do not add a build step, package manager setup, database, or separate app structure.
- Preserve central canvas primitives unless the user explicitly asks to change shared infrastructure.
"""


def agent_command(agent: str, prompt: str, cwd: Path) -> list[str]:
    if agent == "codex":
        return [
            "codex",
            "exec",
            "--cd",
            str(cwd),
            "--skip-git-repo-check",
            "--full-auto",
            "-m",
            CODEX_MODEL,
            prompt,
        ]

    if agent == "claude":
        return [
            "claude",
            "--print",
            "--model",
            CLAUDE_MODEL,
            "--effort",
            CLAUDE_EFFORT,
            "--permission-mode",
            "auto",
            prompt,
        ]

    raise ValueError("agent must be 'codex' or 'claude'")


def run_agent_job(payload: dict, project: str, chat: str, agent: str, cwd: Path, started_at: str) -> None:
    try:
        git_setup = ensure_project_git(cwd)
        history = read_history(project, chat)
        prompt = build_prompt(payload, history, cwd)
        command = agent_command(agent, prompt, cwd)
        result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, check=False)
        output = (result.stdout or "").strip()
        error_output = (result.stderr or "").strip()
        content = chat_content(agent, result.returncode, output, error_output)
        git_commit = (
            commit_project_changes(cwd, agent, payload.get("message", ""))
            if result.returncode == 0
            else {"ok": False, "skipped": True, "message": "Skipped commit because agent run failed."}
        )
        finished_at = now_iso()
        append_history(
            project,
            chat,
            {
                "role": "agent",
                "content": content,
                "agent": agent,
                "exit_code": result.returncode,
                "git": git_commit,
                "ts": finished_at,
            },
        )
        status = {
            "state": "done" if result.returncode == 0 else "error",
            "agent": agent,
            "exit_code": result.returncode,
            "message": content,
            "started_at": started_at,
            "finished_at": finished_at,
            "project_version": project_version(project),
            "git": git_commit,
        }
        if not git_setup.get("ok"):
            status["git_setup_error"] = git_setup.get("error")
        qa = read_qa(project, chat)
        if qa:
            status["qa"] = qa
        write_status(project, chat, status)
    except Exception as exc:
        finished_at = now_iso()
        append_history(project, chat, {"role": "agent", "content": str(exc), "agent": agent, "exit_code": 1, "ts": finished_at})
        write_status(
            project,
            chat,
            {
                "state": "error",
                "agent": agent,
                "exit_code": 1,
                "message": str(exc),
                "started_at": started_at,
                "finished_at": finished_at,
            },
        )


def copy_project(source: str | None, target: str | None) -> str:
    source_name = safe_name(source)
    target_name = safe_name(target)
    source_path = project_dir(source_name)
    target_path = project_dir(target_name)

    if not source_path.exists():
        raise ValueError(f"source project does not exist: {source_name}")
    if target_path.exists():
        return target_name

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {".git", "chats", "attachments", "qa"} & set(names)

    shutil.copytree(source_path, target_path, ignore=ignore)
    (target_path / "chats").mkdir(parents=True, exist_ok=True)
    return target_name


def delete_project(project: str | None) -> str:
    project_name = safe_name(project)
    if project_name == "default":
        raise ValueError("default project cannot be deleted")

    target_path = project_dir(project_name)
    if not target_path.exists():
        raise ValueError(f"project does not exist: {project_name}")

    shutil.rmtree(target_path)
    return project_name


class BridgeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_html(self, status: int, body: str, filename: str | None = None) -> None:
        encoded = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            if filename:
                self.send_header("content-disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:
        self.send_json(200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self.send_json(200, {"ok": True, "root": str(ROOT), "projects": str(PROJECTS_DIR)})
            return

        if parsed.path == "/projects":
            projects = sorted(path.name for path in PROJECTS_DIR.iterdir() if path.is_dir())
            self.send_json(200, {"ok": True, "projects": projects})
            return

        if parsed.path == "/history":
            project = query.get("project", ["default"])[0]
            chat = query.get("chat", ["default"])[0]
            self.send_json(200, {"ok": True, "project": safe_name(project), "chat": safe_name(chat), "messages": read_history(project, chat)})
            return

        if parsed.path == "/status":
            project = query.get("project", ["default"])[0]
            chat = query.get("chat", ["default"])[0]
            status = read_status(project, chat)
            qa = read_qa(project, chat)
            if qa:
                status["qa"] = qa
            self.send_json(200, {"ok": True, "project": safe_name(project), "chat": safe_name(chat), "status": status})
            return

        if parsed.path == "/project-files":
            project = query.get("project", ["default"])[0]
            try:
                self.send_json(200, {"ok": True, "project": safe_name(project), "files": project_jsx_files(project), "version": project_version(project)})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/export-html":
            project = query.get("project", ["default"])[0]
            mode = query.get("mode", ["download"])[0]
            try:
                html = build_export_html(project, mode)
                filename = None if mode == "print" else f"{safe_name(project)}-canvas.html"
                self.send_html(200, html, filename=filename)
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/create-project":
            payload = read_json(self)
            try:
                project = copy_project(payload.get("source") or "default", payload.get("project"))
                self.send_json(200, {"ok": True, "project": project})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/delete-project":
            payload = read_json(self)
            project = payload.get("project") or "default"
            if read_status(project, payload.get("chat") or "default").get("state") == "running":
                self.send_json(409, {"ok": False, "error": "cannot delete a project while its agent is running"})
                return
            try:
                deleted = delete_project(project)
                self.send_json(200, {"ok": True, "deleted": deleted})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/clear-history":
            payload = read_json(self)
            path = chat_path(payload.get("project", "default"), payload.get("chat", "default"))
            if path.exists():
                path.unlink()
            qa = qa_path(payload.get("project", "default"), payload.get("chat", "default"))
            if qa.exists():
                qa.unlink()
            write_status(payload.get("project", "default"), payload.get("chat", "default"), {"state": "idle"})
            self.send_json(200, {"ok": True})
            return

        if parsed.path == "/upload-attachment":
            payload = read_json(self)
            try:
                attachment = save_attachment(payload)
                self.send_json(200, {"ok": True, "attachment": attachment})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/qa-result":
            payload = read_json(self)
            project = payload.get("project") or "default"
            chat = payload.get("chat") or "default"
            qa = {
                "state": payload.get("state") or "unknown",
                "summary": payload.get("summary") or "",
                "checks": payload.get("checks") or {},
                "issues": payload.get("issues") or [],
                "project_version": payload.get("project_version") or project_version(project),
                "ts": now_iso(),
            }
            try:
                write_qa(project, chat, qa)
                status = read_status(project, chat)
                status["qa"] = qa
                write_status(project, chat, status)
                self.send_json(200, {"ok": True, "qa": qa})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if parsed.path != "/run-agent":
            self.send_json(404, {"ok": False, "error": "unknown endpoint"})
            return

        payload = read_json(self)
        project = payload.get("project") or "default"
        chat = payload.get("chat") or "default"
        agent = payload.get("agent") or "codex"
        cwd = project_dir(project)

        if not cwd.exists():
            self.send_json(404, {"ok": False, "error": f"project does not exist: {safe_name(project)}"})
            return

        if read_status(project, chat).get("state") == "running":
            self.send_json(409, {"ok": False, "error": "agent is already running for this project"})
            return

        user_message = {
            "role": "user",
            "content": payload.get("message", ""),
            "attachments": payload.get("attachments") or [],
            "selector": payload.get("selector"),
            "file": payload.get("file"),
            "agent": agent,
            "ts": now_iso(),
        }
        append_history(project, chat, user_message)
        started_at = now_iso()
        write_status(
            project,
            chat,
            {
                "state": "running",
                "agent": agent,
                "file": payload.get("file"),
                "selector": payload.get("selector"),
                "message": payload.get("message", ""),
                "attachments": payload.get("attachments") or [],
                "started_at": started_at,
            },
        )
        threading.Thread(target=run_agent_job, args=(payload, project, chat, agent, cwd, started_at), daemon=True).start()
        self.send_json(202, {"ok": True, "queued": True, "project": safe_name(project), "agent": agent, "started_at": started_at})


def main() -> int:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    print(f"Design bridge running at http://{HOST}:{PORT}/core/canvas.html?project=default")
    print(f"Root: {ROOT}")
    print(f"Projects: {PROJECTS_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping design bridge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
