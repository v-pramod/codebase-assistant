from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.errors import AppError


@dataclass(frozen=True)
class GitHubRepositoryURL:
    canonical_url: str
    owner: str
    name: str


def validate_github_repo_url(raw_url: str) -> GitHubRepositoryURL:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise AppError("invalid_repo_url", "Repository URL must be a public GitHub HTTPS URL.")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise AppError("invalid_repo_url", "Repository URL must not contain credentials.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise AppError(
            "invalid_repo_url", "Repository URL must point to a GitHub owner and repository."
        )
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name or any(part in {".", ".."} for part in (owner, name)):
        raise AppError("invalid_repo_url", "Repository owner and name must be safe path segments.")
    return GitHubRepositoryURL(
        canonical_url=f"https://github.com/{owner}/{name}", owner=owner, name=name
    )
