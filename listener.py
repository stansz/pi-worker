#!/usr/bin/env python3
"""Pi Worker — lightweight HTTP endpoint that spawns one-shot Pi agents.

POST /run  { "prompt": "...", "projects": ["geo-api"] }
    Header: X-Api-Key: <shared-secret>
    Spawns pi -p "context + prompt" --no-session, returns output.
    Auto-commits changes to GitHub on success.

GET /history[?limit=20] — recent run log entries.
GET /health — liveness check.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

# ── Config ──────────────────────────────────────────────────────────

PORT = int(os.environ.get("PI_WORKER_PORT", "9090"))
API_KEY = os.environ.get("PI_WORKER_API_KEY", "")
WORKER_DIR = Path(os.environ.get("PI_WORKER_DIR", os.path.expanduser("~/pi-worker")))
LOG_FILE = WORKER_DIR / "runs.jsonl"
PROJECTS_FILE = WORKER_DIR / "projects.yaml"
TIMEOUT_SEC = int(os.environ.get("PI_WORKER_TIMEOUT", "90"))
LOG_LEVEL = os.environ.get("PI_WORKER_LOG_LEVEL", "WARN")

# Pi binary location — must be on the pi-agent user's PATH
PI_BIN = os.environ.get("PI_BINARY", "pi")

# Max chars of output to store per run
MAX_LOG_OUTPUT = int(os.environ.get("PI_WORKER_MAX_LOG_OUTPUT", "2000"))


def log(level, msg):
    """Structured log line to stderr (systemd journal)."""
    ts = datetime.now(timezone.utc).isoformat()
    print(json.dumps({"ts": ts, "level": level, "msg": msg}), file=sys.stderr, flush=True)


def load_projects() -> dict:
    """Load projects.yaml. Returns {name: {path, docs, services}}."""
    if not PROJECTS_FILE.exists():
        return {}
    raw = PROJECTS_FILE.read_text()
    # Simple YAML parser for our known structure
    projects = {}
    current = None
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            current = stripped[:-1]
            projects[current] = {"path": "", "docs": [], "services": []}
        elif current:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "path":
                projects[current]["path"] = os.path.expanduser(val)
            elif key == "docs":
                # Remove brackets and split
                val = val.strip("[]").strip()
                if val:
                    projects[current]["docs"] = [d.strip().strip('"').strip("'") for d in val.split(",")]
            elif key == "services":
                val = val.strip("[]").strip()
                if val:
                    projects[current]["services"] = [s.strip().strip('"').strip("'") for s in val.split(",")]
    return projects


def get_system_capabilities() -> str:
    """Return a compact description of this VPS for Pi context."""
    return f"""## System (this VPS)
Hostname: {os.uname().nodename}
OS: Linux ({os.uname().sysname} {os.uname().release})
Pi worker dir: {WORKER_DIR}
Projects config: {PROJECTS_FILE}
GitHub user: stansz
"""


def read_project_docs(project_name: str, project_config: dict) -> str:
    """Read AGENTS.md, README.md etc from a project directory."""
    path = Path(project_config["path"])
    doc_names = project_config.get("docs", ["AGENTS.md", "README.md"])

    context = f"## Project: {project_name}\n"
    context += f"Path: {path}\n"

    for doc_name in doc_names:
        doc_path = path / doc_name
        if doc_path.exists():
            try:
                content = doc_path.read_text()
                # Truncate large docs
                if len(content) > 8000:
                    content = content[:8000] + "\n... (truncated)"
                context += f"\n### {doc_name}\n```\n{content}\n```\n"
            except Exception:
                pass

    services = project_config.get("services", [])
    if services:
        context += f"\nServices: {', '.join(services)}\n"

    return context


def build_pi_prompt(user_prompt: str, projects: list[str]) -> str:
    """Build the full prompt to pass to Pi."""
    proj_config = load_projects()

    parts = [get_system_capabilities()]

    for proj_name in projects:
        if proj_name in proj_config:
            parts.append(read_project_docs(proj_name, proj_config[proj_name]))
        else:
            parts.append(f"## Requested project '{proj_name}' not found in projects.yaml\n")

    parts.append(f"## Task\n{user_prompt}")

    parts.append("\n## Rules\n"
                  "- Work in the project directory specified above\n"
                  "- All changes in project directories MUST be committed to git and pushed to GitHub\n"
                  "- Use terminal commands directly — this is your environment\n"
                  "- Be concise and accurate")

    return "\n".join(parts)


def spawn_pi(prompt: str) -> tuple[str, int, float]:
    """Spawn Pi with the given prompt. Returns (stdout, exit_code, duration_sec)."""
    start = time.time()

    try:
        proc = subprocess.run(
            [PI_BIN, "-p", prompt, "--no-session"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            env={**os.environ, "CI": "true"}  # disable TUI
        )
        duration = time.time() - start
        return proc.stdout, proc.returncode, duration

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return f"[TIMEOUT] Pi did not complete within {TIMEOUT_SEC}s", -1, duration
    except FileNotFoundError:
        duration = time.time() - start
        return f"[ERROR] Pi binary '{PI_BIN}' not found on PATH", -1, duration
    except Exception as e:
        duration = time.time() - start
        return f"[ERROR] {e}", -1, duration


def git_commit_and_push(project_path: str, prompt: str) -> bool:
    """Stage all changes, commit, and push in the project directory."""
    path = Path(project_path)
    if not (path / ".git").exists():
        return False

    os.chdir(str(path))

    # Check if there are changes
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    if not status.stdout.strip():
        return True  # nothing to commit

    # Stage all
    subprocess.run(["git", "add", "-A"], capture_output=True, timeout=10)

    # Commit
    short_prompt = prompt[:80].replace("\n", " ").replace('"', "'")
    commit_msg = f"pi-worker: {short_prompt}"
    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True, timeout=10
    )

    # Push
    push = subprocess.run(
        ["git", "push"],
        capture_output=True, text=True, timeout=30
    )

    return push.returncode == 0


def append_log(entry: dict):
    """Append a run entry to the JSONL log file."""
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log("ERROR", f"Failed to write log: {e}")


def read_history(limit: int = 20):
    """Read recent log entries."""
    if not LOG_FILE.exists():
        return []
    entries = []
    try:
        with open(LOG_FILE) as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return entries[-limit:]


class WorkerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Pi worker."""

    def log_message(self, format, *args):
        """Override to use structured logging."""
        if LOG_LEVEL == "DEBUG":
            log("DEBUG", format % args)

    def _check_auth(self) -> bool:
        """Validate the X-Api-Key header."""
        if not API_KEY:
            return True  # no auth configured (dev mode)
        provided = self.headers.get("X-Api-Key", "")
        return provided == API_KEY

    def _send_json(self, status: int, data: dict):
        """Send a JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Optional[dict]:
        """Read and parse JSON request body."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return None

    def do_POST(self):
        if self.path == "/run":
            self._handle_run()
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})
        elif self.path.startswith("/history"):
            self._handle_history()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_run(self):
        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return

        body = self._read_body()
        if body is None:
            return

        prompt = body.get("prompt", "")
        if not prompt:
            self._send_json(400, {"error": "missing 'prompt' field"})
            return

        projects = body.get("projects", [])

        log("INFO", f"RUN projects={projects} prompt={prompt[:100]}")

        # Build Pi prompt with context
        full_prompt = build_pi_prompt(prompt, projects)

        # Spawn Pi
        output, exit_code, duration = spawn_pi(full_prompt)

        log("INFO", f"RESULT exit={exit_code} duration={duration:.1f}s")

        # Git commit on success
        proj_config = load_projects()
        git_results = {}
        if exit_code == 0:
            for proj_name in projects:
                if proj_name in proj_config:
                    proj_path = proj_config[proj_name]["path"]
                    ok = git_commit_and_push(proj_path, prompt)
                    git_results[proj_name] = "committed" if ok else "git_error"

        # Log
        log_entry = {
            "prompt": prompt,
            "projects": projects,
            "exit_code": exit_code,
            "duration": round(duration, 2),
            "output_trunc": output[:MAX_LOG_OUTPUT],
            "git": git_results,
        }
        append_log(log_entry)

        # Respond
        result = {
            "output": output,
            "exit_code": exit_code,
            "duration": round(duration, 2),
            "git": git_results,
        }
        self._send_json(200 if exit_code == 0 else 500, result)

    def _handle_history(self):
        limit = 20
        if "?" in self.path:
            try:
                query = self.path.split("?")[1]
                for param in query.split("&"):
                    if param.startswith("limit="):
                        limit = int(param.split("=")[1])
            except (ValueError, IndexError):
                pass

        entries = read_history(limit)
        self._send_json(200, {"entries": entries, "count": len(entries)})


def main():
    if not API_KEY:
        log("WARN", "No PI_WORKER_API_KEY set — running without auth (dev mode)")

    log("INFO", f"Starting pi-worker on port {PORT}")
    log("INFO", f"Projects file: {PROJECTS_FILE}")
    log("INFO", f"Log file: {LOG_FILE}")

    server = HTTPServer(("127.0.0.1", PORT), WorkerHandler)

    def shutdown(sig, frame):
        log("INFO", "Shutting down")
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log("INFO", "Ready")
    server.serve_forever()


if __name__ == "__main__":
    main()
