import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ChunkingOptions:
    max_lines: int = 80
    overlap_lines: int = 10


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    path: str
    language: str
    content: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_type: str | None = None


def chunk_id(
    repo_id: str,
    snapshot_id: str,
    path: str,
    symbol_name: str | None,
    start_line: int,
    end_line: int,
    content: str,
) -> str:
    content_hash = sha256(content.encode()).hexdigest()
    raw = "|".join(
        [
            repo_id,
            snapshot_id,
            path,
            symbol_name or "",
            str(start_line),
            str(end_line),
            content_hash,
        ]
    )
    return sha256(raw.encode()).hexdigest()


def chunk_file(
    repo_id: str,
    snapshot_id: str,
    path: str,
    content: str,
    options: ChunkingOptions | None = None,
) -> list[CodeChunk]:
    options = options or ChunkingOptions()
    language = _language_for_path(path)
    lines = content.splitlines()
    symbols = _extract_symbols(language, lines)
    if not symbols:
        return _window_chunks(
            repo_id,
            snapshot_id,
            path,
            language,
            lines,
            options,
            None,
            None,
            1,
            len(lines),
        )

    chunks: list[CodeChunk] = []
    for index, symbol in enumerate(symbols):
        start_line, symbol_type, symbol_name = symbol
        end_line = symbols[index + 1][0] - 1 if index + 1 < len(symbols) else len(lines)
        chunks.extend(
            _window_chunks(
                repo_id,
                snapshot_id,
                path,
                language,
                lines,
                options,
                symbol_name,
                symbol_type,
                start_line,
                end_line,
            )
        )
    return chunks


def _window_chunks(
    repo_id: str,
    snapshot_id: str,
    path: str,
    language: str,
    lines: list[str],
    options: ChunkingOptions,
    symbol_name: str | None,
    symbol_type: str | None,
    start_line: int,
    end_line: int,
) -> list[CodeChunk]:
    if not lines:
        return []
    chunks: list[CodeChunk] = []
    current = start_line
    step = max(1, options.max_lines - options.overlap_lines)
    while current <= end_line:
        window_end = min(end_line, current + options.max_lines - 1)
        chunk_text = "\n".join(lines[current - 1 : window_end])
        chunks.append(
            CodeChunk(
                chunk_id=chunk_id(
                    repo_id,
                    snapshot_id,
                    path,
                    symbol_name,
                    current,
                    window_end,
                    chunk_text,
                ),
                path=path,
                language=language,
                content=chunk_text,
                start_line=current,
                end_line=window_end,
                symbol_name=symbol_name,
                symbol_type=symbol_type,
            )
        )
        if window_end == end_line:
            break
        current += step
    return chunks


def _language_for_path(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".md": "markdown",
    }.get(suffix, "text")


def _extract_symbols(language: str, lines: list[str]) -> list[tuple[int, str, str]]:
    symbols: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if language == "python":
            match = re.match(r"(?:async\s+)?def\s+(\w+)|class\s+(\w+)", stripped)
            if match:
                name = match.group(1) or match.group(2)
                symbols.append(
                    (line_number, "function" if match.group(1) else "class", name)
                )
        elif language in {"javascript", "typescript"}:
            pattern = (
                r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"
                r"|(?:export\s+)?class\s+(\w+)"
                r"|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="
            )
            match = re.match(
                pattern,
                stripped,
            )
            if match:
                name = next(group for group in match.groups() if group)
                symbols.append((line_number, "class" if match.group(2) else "function", name))
        elif language == "java":
            pattern = (
                r"(?:public|private|protected)?\s*(?:static\s+)?"
                r"(?:class\s+(\w+)|[\w<>\[\]]+\s+(\w+)\s*\()"
            )
            match = re.match(
                pattern,
                stripped,
            )
            if match:
                name = match.group(1) or match.group(2)
                symbols.append((line_number, "class" if match.group(1) else "method", name))
        elif language == "markdown" and stripped.startswith("#"):
            symbols.append((line_number, "heading", stripped.lstrip("#").strip()))
    return symbols
