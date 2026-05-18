from dataclasses import dataclass
from os import walk
from pathlib import Path
from typing import Any

import pathspec

from app.core.errors import AppError
from app.ingestion.filtering import (
    FileFilterLimits,
    RepositoryFile,
    decide_file,
    gitignore_spec_from_bytes,
)


@dataclass(frozen=True)
class FileTreeEntry:
    path: str
    kind: str
    indexable: bool
    skipped_reason: str | None
    size: int


@dataclass(frozen=True)
class FilePreview:
    path: str
    content: str | None
    previewable: bool
    reason: str | None
    size: int


def list_file_tree(root: Path, limits: FileFilterLimits) -> list[FileTreeEntry]:
    root = root.resolve()
    if not root.exists():
        return []
    gitignore_spec = _gitignore_spec(root)
    entries: list[FileTreeEntry] = []
    for directory, directory_names, file_names in walk(root):
        directory_path = Path(directory)
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if not name.startswith(".")
            and not _matches_gitignore(
                gitignore_spec, (directory_path / name).relative_to(root).as_posix(), True
            )
        ]
        for name in directory_names:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            entries.append(FileTreeEntry(relative, "directory", True, None, 0))

        for name in sorted(file_names):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if _matches_gitignore(gitignore_spec, relative, False):
                continue
            content = path.read_bytes()
            decision = decide_file(RepositoryFile(relative, content), limits, gitignore_spec)
            entries.append(
                FileTreeEntry(relative, "file", decision.indexable, decision.reason, len(content))
            )
    return entries


def read_file_preview(root: Path, requested_path: str, limits: FileFilterLimits) -> FilePreview:
    root = root.resolve()
    file_path = (root / requested_path).resolve()
    if root not in file_path.parents and file_path != root:
        raise AppError("unsafe_path", "Requested file path escapes the repository.", 400)
    if not file_path.exists() or not file_path.is_file():
        raise AppError("file_not_found", "Repository file was not found.", 404)
    content = file_path.read_bytes()
    relative = file_path.relative_to(root).as_posix()
    decision = decide_file(RepositoryFile(relative, content), limits, _gitignore_spec(root))
    if not decision.indexable:
        return FilePreview(relative, None, False, decision.reason, len(content))
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FilePreview(relative, None, False, "binary", len(content))
    return FilePreview(relative, text, True, None, len(content))


def _gitignore_spec(root: Path) -> pathspec.PathSpec[Any] | None:
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    return gitignore_spec_from_bytes(gitignore_path.read_bytes())


def _matches_gitignore(
    gitignore_spec: pathspec.PathSpec[Any] | None, relative: str, is_dir: bool
) -> bool:
    if gitignore_spec is None:
        return False
    path = f"{relative}/" if is_dir else relative
    return gitignore_spec.match_file(path)
