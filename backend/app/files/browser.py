from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError
from app.ingestion.filtering import FileFilterLimits, RepositoryFile, decide_file


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
    entries: list[FileTreeEntry] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append(FileTreeEntry(relative, "directory", True, None, 0))
            continue
        content = path.read_bytes()
        decision = decide_file(RepositoryFile(relative, content), limits)
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
    decision = decide_file(RepositoryFile(relative, content), limits)
    if not decision.indexable:
        return FilePreview(relative, None, False, decision.reason, len(content))
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FilePreview(relative, None, False, "binary", len(content))
    return FilePreview(relative, text, True, None, len(content))
