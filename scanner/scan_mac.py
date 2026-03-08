#!/usr/bin/env python3
"""
Mac project scanner — runs every 2h via cron on Mac.
Copy this file to Mac and run it there.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import urllib.request
import urllib.error

PI_API_URL = "http://100.72.180.20/dashboard/api"
API_KEY = os.getenv("DASHBOARD_API_KEY", "")

# Auto-detect Mac projects root
CANDIDATES = [
    Path.home() / "ai_projects",
    Path.home() / "AI_projects",
    Path.home() / "Projects",
    Path.home() / "Developer",
    Path.home() / "code",
]
SCAN_ROOT = next((p for p in CANDIDATES if p.exists()), Path.home() / "Projects")

EXCLUDE_DIRS = {"node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"}


def get_git_remote(path):
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            url = re.sub(r"git@github\.com:(.+)\.git", r"https://github.com/\1", url)
            url = url.rstrip(".git")
            # Strip embedded credentials (user:token@host → host)
            url = re.sub(r"https://[^@]+@", "https://", url)
            return url
    except Exception:
        pass
    return None


def get_github_repo(git_url):
    if not git_url:
        return None
    m = re.search(r"github\.com[/:](.+/.+?)(?:\.git)?$", git_url)
    return m.group(1) if m else None


def get_last_commit(path):
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci|%s"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 1)
            return parts[0].split(" ")[0] if parts else None, parts[1] if len(parts) > 1 else None
    except Exception:
        pass
    return None, None


def get_tech_stack(path):
    stack = []
    if (path / "package.json").exists():
        try:
            pkg = json.loads((path / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for tech in ["react", "vue", "next", "vite", "express"]:
                if any(tech in k.lower() for k in deps):
                    stack.append(tech.capitalize())
            if "typescript" in deps or (path / "tsconfig.json").exists():
                stack.append("TypeScript")
        except Exception:
            stack.append("Node.js")
    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists():
        stack.append("Python")
    return list(set(stack))


def score_project(path):
    score = 0
    if (path / "CLAUDE.md").exists():
        score += 3
    if (path / ".git").exists():
        score += 2
    if (path / "package.json").exists() or (path / "pyproject.toml").exists():
        score += 1
    return score


def make_id(path):
    return path.name.lower().replace(" ", "-").replace("_", "-")


def post_project(project):
    data = json.dumps(project).encode()
    req = urllib.request.Request(
        f"{PI_API_URL}/projects",
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
        if not entry.is_dir() or entry.name in EXCLUDE_DIRS or entry.name.startswith("."):
            continue
        score = score_project(entry)
        if score == 0:
            continue

        git_url = get_git_remote(entry) if (entry / ".git").exists() else None
        last_date, last_msg = get_last_commit(entry) if (entry / ".git").exists() else (None, None)

        project = {
            "id": make_id(entry),
            "name": entry.name,
            "path_mac": str(entry),
            "github_url": git_url,
            "github_repo": get_github_repo(git_url),
            "last_commit_date": last_date,
            "last_commit_message": last_msg,
            "tech_stack": get_tech_stack(entry),
            "source": "mac",
        }

        print(f"Found: {entry.name} (score={score})")
        post_project(project)


if __name__ == "__main__":
    scan()
