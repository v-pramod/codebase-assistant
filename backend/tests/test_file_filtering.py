import pytest

from app.ingestion.filtering import (
    FileFilterLimits,
    RepositoryFile,
    RepositoryLimitExceeded,
    filter_repository_files,
)


def test_filter_reports_representative_index_and_skip_reasons() -> None:
    files = [
        RepositoryFile("app/main.py", b"print('safe')\n"),
        RepositoryFile("node_modules/pkg/index.js", b"export const x = 1;\n"),
        RepositoryFile("public/app.min.js", b"function x(){}"),
        RepositoryFile(".env", b"SECRET=value\n"),
        RepositoryFile("image.png", b"\x89PNG\x00data"),
        RepositoryFile("large.txt", b"x" * 11),
    ]

    report = filter_repository_files(files, FileFilterLimits(max_file_bytes=10))

    decisions = {decision.path: decision for decision in report.decisions}
    assert decisions["app/main.py"].indexable is False
    assert decisions["app/main.py"].reason == "oversized"
    assert decisions["node_modules/pkg/index.js"].reason == "vendored"
    assert decisions["public/app.min.js"].reason == "minified"
    assert decisions[".env"].reason == "secret"
    assert decisions["image.png"].reason == "binary"
    assert decisions["large.txt"].reason == "oversized"
    assert report.skipped_counts["oversized"] == 2
    assert report.skipped_counts["minified"] == 1


def test_global_limits_fail_before_embedding_instead_of_skipping_everything() -> None:
    files = [RepositoryFile("a.py", b"abc"), RepositoryFile("b.py", b"def")]

    with pytest.raises(RepositoryLimitExceeded):
        filter_repository_files(files, FileFilterLimits(max_repo_bytes=5))

    with pytest.raises(RepositoryLimitExceeded):
        filter_repository_files(files, FileFilterLimits(max_indexed_files=1))
