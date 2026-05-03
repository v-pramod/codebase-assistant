from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FileFilterLimits:
    max_file_bytes: int = 250_000
    max_repo_bytes: int = 200_000_000
    max_indexed_files: int = 20_000


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class FileDecision:
    path: str
    indexable: bool
    reason: str | None = None


@dataclass(frozen=True)
class FileFilterReport:
    decisions: list[FileDecision]
    skipped_counts: dict[str, int]


class RepositoryLimitExceeded(Exception):
    pass


VENDORED_PARTS = {"node_modules", "vendor", ".venv", "venv", "dist", "build", "target"}
SECRET_NAMES = {".env", "credentials.json", "id_rsa", "id_dsa"}
GENERATED_SUFFIXES = (".lock", ".generated.py", ".pb.go")


def filter_repository_files(
    files: list[RepositoryFile], limits: FileFilterLimits
) -> FileFilterReport:
    if sum(file.size for file in files) > limits.max_repo_bytes:
        raise RepositoryLimitExceeded("Repository exceeds configured byte limit before embedding.")

    decisions = [decide_file(file, limits) for file in files]
    indexed_count = sum(1 for decision in decisions if decision.indexable)
    if indexed_count > limits.max_indexed_files:
        raise RepositoryLimitExceeded(
            "Repository exceeds configured indexed file limit before embedding."
        )

    skipped = Counter(
        decision.reason for decision in decisions if decision.reason is not None
    )
    return FileFilterReport(decisions=decisions, skipped_counts=dict(skipped))


def decide_file(file: RepositoryFile, limits: FileFilterLimits) -> FileDecision:
    path = PurePosixPath(file.path)
    name = path.name
    parts = set(path.parts)
    if _looks_binary(file.content):
        return FileDecision(file.path, False, "binary")
    if name in SECRET_NAMES or name.startswith(".env"):
        return FileDecision(file.path, False, "secret")
    if parts & VENDORED_PARTS:
        return FileDecision(file.path, False, "vendored")
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return FileDecision(file.path, False, "minified")
    if name.endswith(GENERATED_SUFFIXES):
        return FileDecision(file.path, False, "generated")
    if file.size > limits.max_file_bytes:
        return FileDecision(file.path, False, "oversized")
    return FileDecision(file.path, True)


def _looks_binary(content: bytes) -> bool:
    if b"\x00" in content:
        return True
    if not content:
        return False
    sample = content[:1024]
    textish = sum(1 for byte in sample if byte in b"\n\r\t" or 32 <= byte <= 126)
    return textish / len(sample) < 0.80
