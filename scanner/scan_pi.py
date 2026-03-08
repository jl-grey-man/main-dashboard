#!/usr/bin/env python3
"""
Pi project scanner — runs every 2h via cron.
Scans /mnt/storage for projects and POSTs to MainDashboard API.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import urllib.request
import urllib.error

API_URL = "http://127.0.0.1:8001"
SCAN_ROOT = Path("/mnt/storage")
API_KEY = os.getenv("DASHBOARD_API_KEY", "")

EXCLUDE_DIRS = {
    "cargo-registry", "backups", "MainDashboard", "claude-code-sync",
    "mail", "mail_learning", "projects", "research", "llm_research",
    "agents", "tasks", "skills", "build", "buildvisualizer",
    "__pycache__", "lost+found", "node_modules", ".git"
}


def get_git_remote(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Convert SSH to HTTPS
            url = re.sub(r"git@github\.com:(.+)\.git", r"https://github.com/\1", url)
            url = url.rstrip(".git")
            # Strip embedded credentials (user:token@host → host)
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
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci|%s"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 1)
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
                content = f.read_text()
                m = re.search(r"https://[a-z0-9-]+\.up\.railway\.app", content)
                if m:
                    return m.group(0)
            except Exception:
                pass
    return None


def get_tech_stack(path: Path) -> list:
    stack = []
    if (path / "package.json").exists():
        try:
            pkg = json.loads((path / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for tech in ["react", "vue", "next", "vite", "express", "fastapi"]:
                if any(tech in k.lower() for k in deps):
                    stack.append(tech.capitalize())
            if "typescript" in deps or (path / "tsconfig.json").exists():
                stack.append("TypeScript")
        except Exception:
            pass
        if not stack:
            stack.append("Node.js")
    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists():
        stack.append("Python")
    if (path / "Cargo.toml").exists():
        stack.append("Rust")
    if (path / "go.mod").exists():
        stack.append("Go")
    return list(set(stack))


def score_project(path: Path) -> int:
    """Higher = more likely to be a real project."""
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


def post_project(project: dict):
    data = json.dumps(project).encode()
    req = urllib.request.Request(
        f"{API_URL}/projects",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  → {project['id']}: {resp.read().decode()}")
    except urllib.error.URLError as e:
        print(f"  ✗ {project['id']}: {e}")


def scan():
    print(f"Scanning {SCAN_ROOT}...")
    for entry in sorted(SCAN_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in EXCLUDE_DIRS or entry.name.startswith("."):
            continue

        score = score_project(entry)
        if score == 0:
            continue

        git_url = get_git_remote(entry) if (entry / ".git").exists() else None
        last_date, last_msg = get_last_commit(entry) if (entry / ".git").exists() else (None, None)

        project = {
            "id": make_id(entry),
            "name": entry.name,
            "path_pi": str(entry),
            "github_url": git_url,
            "github_repo": get_github_repo(git_url),
            "railway_url": get_railway_url(entry),
            "last_commit_date": last_date,
            "last_commit_message": last_msg,
            "tech_stack": get_tech_stack(entry),
            "source": "pi",
        }

        print(f"Found: {entry.name} (score={score})")
        post_project(project)


if __name__ == "__main__":
    scan()
