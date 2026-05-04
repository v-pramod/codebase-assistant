import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.core.errors import AppError


@dataclass(frozen=True)
class RefreshPlan:
    previous_commit: str
    latest_commit: str
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    full_rebuild_available: bool = False


DEPENDENCY_OR_CONFIG_NAMES = {
    "package.json",
    "bun.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "poetry.lock",
    "Dockerfile",
    "docker-compose.yml",
}


def plan_incremental_refresh(
    repo_path: Path, previous_commit: str, latest_commit: str
) -> RefreshPlan:
    if previous_commit == latest_commit:
        files = _git_lines(repo_path, "ls-tree", "-r", "--name-only", latest_commit)
        return RefreshPlan(previous_commit, latest_commit, unchanged=files)

    _ensure_commit_available(repo_path, previous_commit)
    _ensure_commit_available(repo_path, latest_commit)
    changed_entries = _git_lines(
        repo_path, "diff", "--name-status", "--find-renames", previous_commit, latest_commit
    )
    latest_files = set(_git_lines(repo_path, "ls-tree", "-r", "--name-only", latest_commit))
    added: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []

    for entry in changed_entries:
        parts = entry.split("\t")
        status = parts[0]
        if status == "A" and len(parts) == 2:
            added.append(parts[1])
        elif status == "D" and len(parts) == 2:
            deleted.append(parts[1])
        elif status.startswith("R") and len(parts) == 3:
            deleted.append(parts[1])
            added.append(parts[2])
        elif len(parts) >= 2:
            changed.append(parts[-1])

    touched = set(added) | set(changed)
    unchanged = sorted(latest_files - touched)
    warning_paths = sorted(touched & DEPENDENCY_OR_CONFIG_NAMES)
    warnings = [
        "Dependency or config files changed; review retrieval quality and consider a full rebuild."
    ] if warning_paths else []
    return RefreshPlan(
        previous_commit=previous_commit,
        latest_commit=latest_commit,
        added=sorted(added),
        changed=sorted(changed),
        deleted=sorted(deleted),
        unchanged=unchanged,
        warnings=warnings,
        full_rebuild_available=bool(warning_paths),
    )


def read_file_at_commit(repo_path: Path, commit: str, path: str) -> bytes:
    _reject_unsafe_git_path(path)
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit}:{path}"], cwd=repo_path, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        raise AppError(
            "refresh_file_unavailable", f"Could not read {path} at {commit}.", 500
        ) from exc


def latest_default_branch_commit(repo_path: Path) -> str:
    return _git_text(repo_path, "rev-parse", "HEAD")


def clone_or_fetch_repository(repo_url: str, repo_path: Path) -> str:
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    if (repo_path / ".git").is_dir():
        _git_text(repo_path, "fetch", "--prune", "origin")
        remote_head = _git_text(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD")
        branch = remote_head.removeprefix("refs/remotes/origin/")
        _git_text(repo_path, "checkout", branch)
        _git_text(repo_path, "reset", "--hard", f"origin/{branch}")
    else:
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(repo_path)],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise AppError(
                "git_clone_failed", exc.stderr.strip() or "Git clone failed.", 500
            ) from exc
    return latest_default_branch_commit(repo_path)


def list_repository_files_at_head(repo_path: Path) -> list[str]:
    return _git_lines(repo_path, "ls-files")


def _ensure_commit_available(repo_path: Path, commit: str) -> None:
    try:
        _git_text(repo_path, "cat-file", "-e", f"{commit}^{{commit}}")
    except AppError as exc:
        raise AppError(
            "refresh_commit_unavailable",
            "Refresh requires the exact previous and latest commits to be available locally.",
            409,
        ) from exc


def _git_lines(repo_path: Path, *args: str) -> list[str]:
    text = _git_text(repo_path, *args)
    return [line for line in text.splitlines() if line]


def _git_text(repo_path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise AppError(
            "git_refresh_failed", exc.stderr.strip() or "Git refresh planning failed.", 500
        ) from exc
    return completed.stdout.strip()


def _reject_unsafe_git_path(path: str) -> None:
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise AppError(
            "invalid_file_path", "Repository file paths must stay inside the clone.", 400
        )
