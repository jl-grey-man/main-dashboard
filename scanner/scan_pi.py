#!/usr/bin/env python3
"""
Pi project scanner — runs every 2h via cron.
Uses claude CLI to analyze each project directory and extract metadata.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
import urllib.request
import urllib.error

API_URL = "http://127.0.0.1:8001"
SCAN_ROOT = Path("/mnt/storage")
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "/home/jens/.local/bin/claude")

EXCLUDE_DIRS = {
    "cargo-registry", "backups", "MainDashboard", "claude-code-sync",
    "mail", "mail_learning", "projects", "research", "llm_research",
    "agents", "tasks", "skills", "build", "buildvisualizer",
    "__pycache__", "lost+found", "node_modules", ".git",
}


# ── git helpers ────────────────────────────────────────────────────────────

def get_git_remote(path: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            url = r.stdout.strip()
            url = re.sub(r"git@github\.com:(.+)\.git", r"https://github.com/\1", url)
            url = url.rstrip(".git")
            url = re.sub(r"https://[^@]+@", "https://", url)
            return url
    except Exception:
        pass
    return None


def get_github_repo(git_url: str | None) -> str | None:
    if not git_url:
        return None
    m = re.search(r"github\.com[/:](.+/.+?)(?:\.git)?$", git_url)
    return m.group(1) if m else None


def get_last_commit(path: Path) -> tuple[str | None, str | None]:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%ci|%s"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split("|", 1)
            date = parts[0].split(" ")[0] if parts else None
            msg = parts[1] if len(parts) > 1 else None
            return date, msg
    except Exception:
        pass
    return None, None


def get_railway_url(path: Path) -> str | None:
    for name in ["railway.json", ".railway", "railway.toml"]:
        f = path / name
        if f.exists():
            try:
                m = re.search(r"https://[a-z0-9-]+\.up\.railway\.app", f.read_text())
                if m:
                    return m.group(0)
            except Exception:
                pass
    return None


def score_project(path: Path) -> int:
    score = 0
    if (path / "CLAUDE.md").exists():
        score += 3
    if (path / ".git").exists():
        score += 2
    if (path / "package.json").exists() or (path / "pyproject.toml").exists():
        score += 1
    return score


def make_id(path: Path) -> str:
    return path.name.lower().replace(" ", "-").replace("_", "-")


# ── context gathering ──────────────────────────────────────────────────────

def read_snippet(path: Path, max_chars: int = 2000) -> str:
    try:
        return path.read_text(errors="ignore")[:max_chars]
    except Exception:
        return ""


def gather_context(path: Path) -> str:
    parts = [f"Directory: {path.name}"]

    # Top-level file listing
    try:
        entries = sorted(
            e.name + ("/" if e.is_dir() else "")
            for e in path.iterdir()
            if not e.name.startswith(".")
               and e.name not in {"node_modules", "__pycache__", "venv", "dist", "build", ".next"}
        )
        parts.append("Files: " + ", ".join(entries[:40]))
    except Exception:
        pass

    for fname in ["README.md", "CLAUDE.md"]:
        f = path / fname
        if f.exists():
            parts.append(f"\n--- {fname} ---\n{read_snippet(f, 2500)}")

    # package.json name + description
    pkg = path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            if data.get("name") or data.get("description"):
                parts.append(f"\npackage.json name: {data.get('name','')}  description: {data.get('description','')}")
        except Exception:
            pass

    # pyproject.toml first 500 chars
    pp = path / "pyproject.toml"
    if pp.exists():
        parts.append(f"\n--- pyproject.toml ---\n{read_snippet(pp, 500)}")

    return "\n".join(parts)


# ── Claude CLI analysis ────────────────────────────────────────────────────

PROMPT_TEMPLATE = """You are analysing a software project directory on a Raspberry Pi developer machine.

Here is the context for the directory "{name}":

{context}

Return ONLY valid JSON (no markdown, no explanation) in this exact shape:
{{
  "name": "human-readable project name",
  "description": "1-2 sentence description of what this project does",
  "tech_stack": ["list", "of", "technologies"],
  "is_collection": false,
  "sub_projects": []
}}

Rules:
- "name": clean human-readable name, not a slug
- "description": be specific about purpose, not generic ("A web app")
- "tech_stack": real tech names like ["React", "TypeScript", "FastAPI", "Python", "Rust"]
- "is_collection": set true ONLY if this directory is a monorepo or workspace containing multiple distinct sub-projects (each with their own package.json or .git)
- "sub_projects": if is_collection is true, list the sub-directory names that are real standalone projects
"""


def claude_analyse(path: Path) -> dict | None:
    context = gather_context(path)
    prompt = PROMPT_TEMPLATE.format(name=path.name, context=context)

    # Strip surrogates that break UTF-8 encoding in subprocess stdin
    prompt = prompt.encode("utf-8", errors="replace").decode("utf-8")

    env = {**os.environ, "CLAUDECODE": ""}  # unset CLAUDECODE so nested calls work
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        output = r.stdout.strip()
        # Extract JSON from output (claude might add preamble)
        m = re.search(r"\{[\s\S]+\}", output)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"  ⚠ claude error for {path.name}: {e}", file=sys.stderr)
    return None


# ── API posting ────────────────────────────────────────────────────────────

def post_project(project: dict):
    data = json.dumps(project).encode()
    req = urllib.request.Request(
        f"{API_URL}/projects",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  → {project['id']}: {resp.read().decode()}")
    except urllib.error.URLError as e:
        print(f"  ✗ {project['id']}: {e}", file=sys.stderr)


def build_and_post(path: Path, ai: dict | None):
    has_git = (path / ".git").exists()
    git_url = get_git_remote(path) if has_git else None
    last_date, last_msg = get_last_commit(path) if has_git else (None, None)

    project = {
        "id": make_id(path),
        "name": (ai or {}).get("name") or path.name,
        "description": (ai or {}).get("description"),
        "path_pi": str(path),
        "github_url": git_url,
        "github_repo": get_github_repo(git_url),
        "railway_url": get_railway_url(path),
        "last_commit_date": last_date,
        "last_commit_message": last_msg,
        "tech_stack": (ai or {}).get("tech_stack") or [],
        "source": "pi",
    }
    post_project(project)


# ── main scan ──────────────────────────────────────────────────────────────

def scan():
    print(f"Scanning {SCAN_ROOT} with AI analysis...")
    for entry in sorted(SCAN_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in EXCLUDE_DIRS or entry.name.startswith("."):
            continue
        if score_project(entry) == 0:
            continue

        print(f"\n[{entry.name}] analysing...")
        ai = claude_analyse(entry)

        if ai and ai.get("is_collection") and ai.get("sub_projects"):
            print(f"  → collection, scanning sub-projects: {ai['sub_projects']}")
            for sub_name in ai["sub_projects"]:
                sub = entry / sub_name
                if sub.is_dir():
                    print(f"  [{sub_name}] analysing sub-project...")
                    sub_ai = claude_analyse(sub)
                    build_and_post(sub, sub_ai)
        else:
            build_and_post(entry, ai)


if __name__ == "__main__":
    scan()
