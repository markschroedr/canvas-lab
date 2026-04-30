#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
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


def status_path(project: str | None, chat: str | None) -> Path:
    return project_dir(project) / "chats" / f"{safe_name(chat)}.status.json"


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


def build_prompt(payload: dict, history: list[dict], cwd: Path) -> str:
    file_path = payload.get("file") or "canvas.jsx"
    selector = payload.get("selector") or "(none)"
    message = payload.get("message") or ""
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
        history_lines.append(f"{prefix}\n{content}")

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

Full chat history:
{chr(10).join(history_lines) if history_lines else "(empty)"}

Current user request:
{message}

Rules:
- Edit files directly in this project folder when the request asks for a change.
- If a current marked element is present, treat it as the user's target unless the request clearly says otherwise.
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
        history = read_history(project, chat)
        prompt = build_prompt(payload, history, cwd)
        command = agent_command(agent, prompt, cwd)
        result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, check=False)
        output = (result.stdout or "").strip()
        error_output = (result.stderr or "").strip()
        content = chat_content(agent, result.returncode, output, error_output)
        finished_at = now_iso()
        append_history(
            project,
            chat,
            {
                "role": "agent",
                "content": content,
                "agent": agent,
                "exit_code": result.returncode,
                "ts": finished_at,
            },
        )
        write_status(
            project,
            chat,
            {
                "state": "done" if result.returncode == 0 else "error",
                "agent": agent,
                "exit_code": result.returncode,
                "message": content,
                "started_at": started_at,
                "finished_at": finished_at,
                "project_version": project_version(project),
            },
        )
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
        return {"chats"} & set(names)

    shutil.copytree(source_path, target_path, ignore=ignore)
    (target_path / "chats").mkdir(parents=True, exist_ok=True)
    return target_name


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
            self.send_json(200, {"ok": True, "project": safe_name(project), "chat": safe_name(chat), "status": read_status(project, chat)})
            return

        if parsed.path == "/project-files":
            project = query.get("project", ["default"])[0]
            try:
                self.send_json(200, {"ok": True, "project": safe_name(project), "files": project_jsx_files(project), "version": project_version(project)})
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

        if parsed.path == "/clear-history":
            payload = read_json(self)
            path = chat_path(payload.get("project", "default"), payload.get("chat", "default"))
            if path.exists():
                path.unlink()
            write_status(payload.get("project", "default"), payload.get("chat", "default"), {"state": "idle"})
            self.send_json(200, {"ok": True})
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
