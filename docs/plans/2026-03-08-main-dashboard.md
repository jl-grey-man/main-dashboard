# MainDashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a self-updating project dashboard served at `http://100.72.180.20/dashboard` that tracks all projects across Pi and Mac, backed by a GitHub repo as source of truth.

**Architecture:** FastAPI hub on Pi (port 8001) accepts project data from scanners on Pi and Mac, writes to `projects.json`, and commits to GitHub. Static HTML dashboard reads `projects.json` locally. Cron jobs on Pi and Mac scan for projects every 2 hours and POST to the Pi API over Tailscale.

**Tech Stack:** Python 3 / FastAPI, vanilla HTML/JS, nginx, systemd, GitHub (data store), cron

---

## Task 1: Initialize GitHub repo and local clone

**Files:**
- Create: `/mnt/storage/MainDashboard/projects.json`
- Create: `/mnt/storage/MainDashboard/ignored.json`
- Create: `/mnt/storage/MainDashboard/.gitignore`

**Step 1: Create GitHub repo**

Go to https://github.com/new and create `jl-grey-man/main-dashboard` (public or private, your choice).

**Step 2: Initialize local git repo**

```bash
cd /mnt/storage/MainDashboard
git init
git remote add origin https://github.com/jl-grey-man/main-dashboard.git
```

**Step 3: Create initial data files**

Create `/mnt/storage/MainDashboard/projects.json`:
```json
{
  "projects": [],
  "pending": []
}
```

Create `/mnt/storage/MainDashboard/ignored.json`:
```json
{
  "ignored_paths": []
}
```

Create `/mnt/storage/MainDashboard/.gitignore`:
```
__pycache__/
*.pyc
.env
venv/
*.log
```

**Step 4: Initial commit and push**

```bash
cd /mnt/storage/MainDashboard
git add .
git commit -m "init: initial project structure"
git push -u origin main
```

Expected: repo appears at https://github.com/jl-grey-man/main-dashboard

---

## Task 2: FastAPI server

**Files:**
- Create: `/mnt/storage/MainDashboard/api/main.py`
- Create: `/mnt/storage/MainDashboard/api/requirements.txt`
- Create: `/mnt/storage/MainDashboard/api/.env.example`

**Step 1: Create requirements.txt**

```
fastapi==0.110.0
uvicorn==0.27.1
python-dotenv==1.0.1
GitPython==3.1.41
```

**Step 2: Install dependencies**

```bash
cd /mnt/storage/MainDashboard/api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Step 3: Create .env.example**

```
GITHUB_TOKEN=your_github_token_here
GITHUB_REPO=jl-grey-man/main-dashboard
REPO_PATH=/mnt/storage/MainDashboard
API_KEY=choose_a_secret_key_here
```

Copy to `.env` and fill in real values:
```bash
cp /mnt/storage/MainDashboard/api/.env.example /mnt/storage/MainDashboard/api/.env
```

**Step 4: Create api/main.py**

```python
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="MainDashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REPO_PATH = Path(os.getenv("REPO_PATH", "/mnt/storage/MainDashboard"))
PROJECTS_FILE = REPO_PATH / "projects.json"
IGNORED_FILE = REPO_PATH / "ignored.json"
API_KEY = os.getenv("API_KEY", "")


def load_data():
    with open(PROJECTS_FILE) as f:
        return json.load(f)


def save_data(data: dict):
    with open(PROJECTS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    git_commit("update: sync projects.json")


def load_ignored():
    with open(IGNORED_FILE) as f:
        return json.load(f)


def save_ignored(data: dict):
    with open(IGNORED_FILE, "w") as f:
        json.dump(data, f, indent=2)
    git_commit("update: sync ignored.json")


def git_commit(message: str):
    try:
        subprocess.run(["git", "add", "projects.json", "ignored.json"], cwd=REPO_PATH, check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO_PATH
        )
        if result.returncode != 0:  # there are staged changes
            subprocess.run(["git", "commit", "-m", message], cwd=REPO_PATH, check=True)
            subprocess.run(["git", "push"], cwd=REPO_PATH, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}")


def check_auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


class Project(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    path_pi: Optional[str] = None
    path_mac: Optional[str] = None
    status_label: Optional[str] = "active"
    github_repo: Optional[str] = None
    github_url: Optional[str] = None
    railway_url: Optional[str] = None
    served_at: Optional[str] = None
    last_commit_date: Optional[str] = None
    last_commit_message: Optional[str] = None
    tech_stack: Optional[list] = []
    source: Optional[str] = "manual"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/projects")
def list_projects(x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    return load_data()


@app.post("/projects")
def upsert_project(project: Project, x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    data = load_data()
    now = datetime.now(timezone.utc).isoformat()

    # Check ignored
    ignored = load_ignored()
    path = project.path_pi or project.path_mac or ""
    if path in ignored.get("ignored_paths", []):
        return {"status": "ignored", "message": "Project is on the ignore list"}

    # Check if already exists in projects or pending
    for section in ("projects", "pending"):
        for i, p in enumerate(data[section]):
            if p["id"] == project.id:
                data[section][i] = {**p, **project.dict(), "updated_at": now}
                save_data(data)
                return {"status": "updated", "section": section}

    # New project — goes to pending
    entry = {**project.dict(), "state": "pending", "added_at": now, "updated_at": now}
    data["pending"].append(entry)
    save_data(data)
    return {"status": "added_to_pending"}


@app.post("/projects/{project_id}/approve")
def approve_project(project_id: str, x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    data = load_data()
    now = datetime.now(timezone.utc).isoformat()

    for i, p in enumerate(data["pending"]):
        if p["id"] == project_id:
            p["state"] = "approved"
            p["updated_at"] = now
            data["projects"].append(p)
            data["pending"].pop(i)
            save_data(data)
            return {"status": "approved"}

    raise HTTPException(status_code=404, detail="Project not found in pending")


@app.post("/projects/{project_id}/ignore")
def ignore_project(project_id: str, x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    data = load_data()
    ignored = load_ignored()

    for section in ("projects", "pending"):
        for i, p in enumerate(data[section]):
            if p["id"] == project_id:
                path = p.get("path_pi") or p.get("path_mac") or ""
                if path and path not in ignored["ignored_paths"]:
                    ignored["ignored_paths"].append(path)
                data[section].pop(i)
                save_data(data)
                save_ignored(ignored)
                return {"status": "ignored"}

    raise HTTPException(status_code=404, detail="Project not found")


@app.post("/projects/{project_id}/update")
def update_project(project_id: str, fields: dict, x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    data = load_data()
    now = datetime.now(timezone.utc).isoformat()

    for section in ("projects", "pending"):
        for i, p in enumerate(data[section]):
            if p["id"] == project_id:
                data[section][i] = {**p, **fields, "updated_at": now}
                save_data(data)
                return {"status": "updated"}

    raise HTTPException(status_code=404, detail="Project not found")


@app.post("/scan")
def trigger_scan(x_api_key: Optional[str] = Header(None)):
    check_auth(x_api_key)
    script = REPO_PATH / "scanner" / "scan_pi.py"
    subprocess.Popen(["python3", str(script)])
    return {"status": "scan triggered"}
```

**Step 5: Test the server manually**

```bash
cd /mnt/storage/MainDashboard/api
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Open: `http://100.72.180.20:8001/health`
Expected: `{"status": "ok"}`

**Step 6: Commit**

```bash
cd /mnt/storage/MainDashboard
git add api/
git commit -m "feat: add FastAPI hub server"
git push
```

---

## Task 3: systemd service for the API

**Files:**
- Create: `/etc/systemd/system/maindashboard-api.service`

**Step 1: Create service file**

```bash
sudo tee /etc/systemd/system/maindashboard-api.service > /dev/null << 'EOF'
[Unit]
Description=MainDashboard API
After=network.target

[Service]
User=jens
WorkingDirectory=/mnt/storage/MainDashboard/api
ExecStart=/mnt/storage/MainDashboard/api/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5
EnvironmentFile=/mnt/storage/MainDashboard/api/.env

[Install]
WantedBy=multi-user.target
EOF
```

**Step 2: Enable and start**

```bash
sudo systemctl daemon-reload
sudo systemctl enable maindashboard-api
sudo systemctl start maindashboard-api
sudo systemctl status maindashboard-api
```

Expected: `Active: active (running)`

---

## Task 4: nginx proxy for the API and static dashboard

**Files:**
- Modify: `/etc/nginx/sites-available/default`

**Step 1: Add routes to nginx config**

Add inside the `server {}` block (after existing location blocks):

```nginx
# MainDashboard static files
location /dashboard {
    alias /mnt/storage/MainDashboard/public;
    index index.html;
    try_files $uri $uri/ /dashboard/index.html;
}

# MainDashboard API proxy
location /dashboard/api/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

**Step 2: Test and reload nginx**

```bash
sudo nginx -t
sudo nginx -s reload
```

Expected: `nginx: configuration file ... test is successful`

**Step 3: Verify**

`http://100.72.180.20/dashboard/api/health` → `{"status": "ok"}`

---

## Task 5: Pi scanner script

**Files:**
- Create: `/mnt/storage/MainDashboard/scanner/scan_pi.py`

**Step 1: Create scan_pi.py**

```python
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
```

**Step 2: Make executable**

```bash
chmod +x /mnt/storage/MainDashboard/scanner/scan_pi.py
```

**Step 3: Test**

```bash
python3 /mnt/storage/MainDashboard/scanner/scan_pi.py
```

Expected: projects posted, check `http://100.72.180.20/dashboard/api/projects`

**Step 4: Set up cron**

```bash
crontab -e
```

Add:
```
0 */2 * * * DASHBOARD_API_KEY=your_key /usr/bin/python3 /mnt/storage/MainDashboard/scanner/scan_pi.py >> /mnt/storage/MainDashboard/scanner/scan-pi.log 2>&1
```

**Step 5: Commit**

```bash
cd /mnt/storage/MainDashboard
git add scanner/scan_pi.py
git commit -m "feat: add Pi scanner script"
git push
```

---

## Task 6: Mac scanner script

**Files:**
- Create: `/mnt/storage/MainDashboard/scanner/scan_mac.py`

**Step 1: Create scan_mac.py**

This is the same logic as scan_pi.py but targets Mac paths and POSTs to the Pi over Tailscale.

```python
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


# --- Reuse same helper functions as scan_pi.py ---

def get_git_remote(path):
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            url = re.sub(r"git@github\.com:(.+)\.git", r"https://github.com/\1", url)
            return url.rstrip(".git")
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
```

**Step 2: Instructions for Mac setup**

On Mac:
```bash
# Copy the script
cp /path/to/scan_mac.py ~/scan_mac.py

# Test it (make sure Tailscale is on)
DASHBOARD_API_KEY=your_key python3 ~/scan_mac.py

# Add cron
crontab -e
# Add:
# 0 */2 * * * DASHBOARD_API_KEY=your_key /usr/bin/python3 ~/scan_mac.py >> ~/scan-mac.log 2>&1
```

**Step 3: Commit**

```bash
cd /mnt/storage/MainDashboard
git add scanner/scan_mac.py
git commit -m "feat: add Mac scanner script"
git push
```

---

## Task 7: Dashboard UI

**Files:**
- Create: `/mnt/storage/MainDashboard/public/index.html`

**Step 1: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Project Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 24px; }
    h1 { font-size: 1.4rem; color: #fff; margin-bottom: 8px; }
    .subtitle { font-size: 0.8rem; color: #666; margin-bottom: 32px; }
    h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: #555; margin: 32px 0 12px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
    .card {
      background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px;
      padding: 16px; position: relative;
    }
    .card.pending { border-color: #4a3a00; background: #1a1500; }
    .card-name { font-size: 1rem; font-weight: 600; color: #fff; margin-bottom: 4px; }
    .card-desc { font-size: 0.8rem; color: #888; margin-bottom: 12px; min-height: 20px; }
    .meta { display: flex; flex-direction: column; gap: 4px; }
    .meta-row { display: flex; align-items: center; gap: 6px; font-size: 0.75rem; color: #666; }
    .meta-row a { color: #4a9eff; text-decoration: none; }
    .meta-row a:hover { text-decoration: underline; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 0.7rem; font-weight: 500;
    }
    .badge.active { background: #0a3a0a; color: #4caf50; }
    .badge.paused { background: #3a2a00; color: #ff9800; }
    .badge.archived { background: #2a0a0a; color: #f44336; }
    .badge.pending-badge { background: #3a3000; color: #ffc107; }
    .stack { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
    .stack-tag {
      background: #2a2a2a; color: #aaa; font-size: 0.65rem;
      padding: 2px 6px; border-radius: 4px;
    }
    .card-actions { display: flex; gap: 8px; margin-top: 12px; }
    .btn {
      padding: 4px 12px; border-radius: 4px; border: none;
      cursor: pointer; font-size: 0.75rem; font-weight: 500;
    }
    .btn-approve { background: #1a4a1a; color: #4caf50; }
    .btn-approve:hover { background: #2a6a2a; }
    .btn-ignore { background: #2a1a1a; color: #f44336; }
    .btn-ignore:hover { background: #4a2a2a; }
    .source-badge { position: absolute; top: 12px; right: 12px; font-size: 0.65rem; color: #444; }
    .empty { color: #444; font-size: 0.85rem; padding: 16px 0; }
    .last-updated { font-size: 0.7rem; color: #444; margin-top: 32px; }
  </style>
</head>
<body>

<h1>Project Dashboard</h1>
<p class="subtitle" id="last-updated">Loading...</p>

<h2 id="pending-heading">Pending Review</h2>
<div class="grid" id="pending-grid"></div>

<h2>Active Projects</h2>
<div class="grid" id="active-grid"></div>

<script>
const API = '/dashboard/api';
const API_KEY = localStorage.getItem('dashboard_api_key') || '';

async function api(path, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' }
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  return r.json();
}

function badge(label) {
  const cls = label === 'active' ? 'active' : label === 'paused' ? 'paused' : 'archived';
  return `<span class="badge ${cls}">${label}</span>`;
}

function card(p, isPending) {
  const div = document.createElement('div');
  div.className = 'card' + (isPending ? ' pending' : '');
  div.innerHTML = `
    <span class="source-badge">${p.source || ''}</span>
    <div class="card-name">${p.name}</div>
    <div class="card-desc">${p.description || '<em style="color:#555">no description</em>'}</div>
    <div class="meta">
      ${p.path_pi ? `<div class="meta-row">📂 <span>${p.path_pi}</span></div>` : ''}
      ${p.path_mac ? `<div class="meta-row">💻 <span>${p.path_mac}</span></div>` : ''}
      ${p.served_at ? `<div class="meta-row">🌐 <a href="${p.served_at}" target="_blank">${p.served_at}</a></div>` : ''}
      ${p.github_url ? `<div class="meta-row">🐙 <a href="${p.github_url}" target="_blank">${p.github_repo || p.github_url}</a></div>` : ''}
      ${p.railway_url ? `<div class="meta-row">🚂 <a href="${p.railway_url}" target="_blank">${p.railway_url}</a></div>` : ''}
      ${p.last_commit_date ? `<div class="meta-row">🕐 ${p.last_commit_date} — ${p.last_commit_message || ''}</div>` : ''}
      ${!isPending ? `<div class="meta-row" style="margin-top:4px">${badge(p.status_label || 'active')}</div>` : ''}
    </div>
    ${p.tech_stack?.length ? `<div class="stack">${p.tech_stack.map(t => `<span class="stack-tag">${t}</span>`).join('')}</div>` : ''}
    ${isPending ? `<div class="card-actions">
      <button class="btn btn-approve" onclick="approve('${p.id}')">✓ Add</button>
      <button class="btn btn-ignore" onclick="ignore('${p.id}')">✗ Ignore</button>
    </div>` : ''}
  `;
  return div;
}

async function approve(id) {
  await api(`/projects/${id}/approve`, 'POST');
  load();
}

async function ignore(id) {
  await api(`/projects/${id}/ignore`, 'POST');
  load();
}

async function load() {
  const data = await api('/projects');
  const pending = data.pending || [];
  const projects = data.projects || [];

  const pendingGrid = document.getElementById('pending-grid');
  const pendingHeading = document.getElementById('pending-heading');
  const activeGrid = document.getElementById('active-grid');

  pendingHeading.style.display = pending.length ? '' : 'none';
  pendingGrid.innerHTML = '';
  pending.forEach(p => pendingGrid.appendChild(card(p, true)));

  activeGrid.innerHTML = '';
  if (projects.length === 0) {
    activeGrid.innerHTML = '<p class="empty">No projects yet. Run the scanner or add one manually.</p>';
  } else {
    projects.forEach(p => activeGrid.appendChild(card(p, false)));
  }

  document.getElementById('last-updated').textContent =
    `Last loaded: ${new Date().toLocaleTimeString()}  ·  ${projects.length} projects  ·  ${pending.length} pending`;
}

// Prompt for API key if not set
if (!API_KEY) {
  const key = prompt('Enter dashboard API key (set once, stored in browser):');
  if (key) localStorage.setItem('dashboard_api_key', key);
  location.reload();
}

load();
setInterval(load, 30000); // refresh every 30s
</script>
</body>
</html>
```

**Step 2: Commit**

```bash
cd /mnt/storage/MainDashboard
git add public/index.html
git commit -m "feat: add dashboard UI"
git push
```

**Step 3: Verify**

Open `http://100.72.180.20/dashboard` — should show the dashboard.

---

## Task 8: `/maindashboard` Claude skill

**Files:**
- Create: `/home/jens/.claude/skills/maindashboard/skill.md`

**Step 1: Create skill directory and file**

```bash
mkdir -p /home/jens/.claude/skills/maindashboard
```

Create `/home/jens/.claude/skills/maindashboard/skill.md`:

```markdown
# /maindashboard skill

Add or update a project in the MainDashboard.

## Usage
Invoked as `/maindashboard` from any Claude Code session (Pi or Mac).

## What it does
1. Detects context: are we in a project directory? Check for CLAUDE.md, package.json, .git
2. Extracts: project name (folder name), path, git remote, railway URL, tech stack, last commit
3. Asks user to confirm/fill in: description, status_label, served_at
4. POSTs to `http://100.72.180.20/dashboard/api/projects` with X-API-Key header
5. Reports result: added to pending / updated

## API endpoint
POST http://100.72.180.20/dashboard/api/projects
Header: X-API-Key: [from DASHBOARD_API_KEY env or ask user]

## Implementation steps
When invoked:
1. Run: `pwd` to get current directory
2. Check for `.git`, `CLAUDE.md`, `package.json`, `railway.json`
3. Run `git remote get-url origin` if .git exists
4. Run `git log -1 --format="%ci|%s"` if .git exists
5. Read package.json for tech stack if exists
6. Ask: "Description for this project?" (one line)
7. Ask: "Status? (active/paused/archived)" default active
8. Ask: "Served at URL? (leave blank if none)"
9. Build project JSON and POST to API
10. Show result
```

**Step 2: Register the skill**

The skill file is automatically picked up by Claude Code from `~/.claude/skills/`. No further config needed.

---

## Task 9: Pre-populate existing projects

**Step 1: Run the Pi scanner**

```bash
DASHBOARD_API_KEY=your_key python3 /mnt/storage/MainDashboard/scanner/scan_pi.py
```

**Step 2: Open dashboard and review pending**

`http://100.72.180.20/dashboard`

Approve projects you want to keep. Ignore ones you don't.

**Step 3: Run `/maindashboard` for HiChord and cosmosbeat-mobile**

These may need `served_at` and `description` filled in manually.

---

## Task 10: Update CLAUDE.md and memory

**Step 1: Create project CLAUDE.md**

```bash
cd /mnt/storage/MainDashboard
```

Create `CLAUDE.md` with project architecture, API key notes, scanner paths.

**Step 2: Update memory**

Update `/home/jens/.claude/projects/-mnt-storage-cosmobeat2/memory/MEMORY.md` to add MainDashboard entry.

**Step 3: Final commit**

```bash
cd /mnt/storage/MainDashboard
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md"
git push
```

---

## Summary

| Task | What |
|------|------|
| 1 | GitHub repo + local clone |
| 2 | FastAPI server (port 8001) |
| 3 | systemd service |
| 4 | nginx proxy |
| 5 | Pi scanner + cron |
| 6 | Mac scanner + cron |
| 7 | Dashboard HTML UI |
| 8 | /maindashboard skill |
| 9 | Pre-populate with existing projects |
| 10 | CLAUDE.md + memory |
