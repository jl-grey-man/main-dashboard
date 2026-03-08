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
