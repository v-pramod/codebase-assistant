from app.chunking.chunker import ChunkingOptions, chunk_file, chunk_id


def test_python_chunks_preserve_symbol_metadata_and_line_ranges() -> None:
    chunks = chunk_file(
        "repo",
        "snap",
        "app/service.py",
        "class Service:\n    pass\n\ndef run():\n    return 1\n",
    )

    assert [
        (chunk.symbol_type, chunk.symbol_name, chunk.start_line, chunk.end_line) for chunk in chunks
    ] == [
        ("class", "Service", 1, 3),
        ("function", "run", 4, 5),
    ]


def test_javascript_typescript_java_and_markdown_have_useful_symbols() -> None:
    js = chunk_file("repo", "snap", "src/app.js", "export function mount() {\n}\n")
    ts = chunk_file("repo", "snap", "src/app.ts", "export const load = () => {\n}\n")
    java = chunk_file(
        "repo",
        "snap",
        "src/App.java",
        "public class App {\npublic void run() {\n}\n}\n",
    )
    md = chunk_file("repo", "snap", "README.md", "# Intro\ntext\n## Usage\nmore\n")

    assert js[0].symbol_name == "mount"
    assert ts[0].symbol_name == "load"
    assert java[0].symbol_name == "App"
    assert md[0].symbol_name == "Intro"
    assert md[1].start_line == 3


def test_unsupported_text_uses_fallback_windows_with_line_ranges() -> None:
    chunks = chunk_file(
        "repo",
        "snap",
        "notes.txt",
        "one\ntwo\nthree\nfour\n",
        ChunkingOptions(max_lines=2, overlap_lines=0),
    )

    assert [
        (chunk.language, chunk.start_line, chunk.end_line, chunk.symbol_name) for chunk in chunks
    ] == [
        ("text", 1, 2, None),
        ("text", 3, 4, None),
    ]


def test_oversized_symbols_split_into_overlapping_windows_with_parent_metadata() -> None:
    content = "def large():\n" + "\n".join(f"    value_{index} = {index}" for index in range(1, 7))

    chunks = chunk_file(
        "repo",
        "snap",
        "app/large.py",
        content,
        ChunkingOptions(max_lines=3, overlap_lines=1),
    )

    assert [(chunk.start_line, chunk.end_line, chunk.symbol_name) for chunk in chunks] == [
        (1, 3, "large"),
        (3, 5, "large"),
        (5, 7, "large"),
    ]


def test_chunk_identity_is_deterministic_and_content_aware() -> None:
    same = chunk_id("repo", "snap", "a.py", "run", 1, 2, "def run():\n    return 1")
    same_again = chunk_id("repo", "snap", "a.py", "run", 1, 2, "def run():\n    return 1")
    changed = chunk_id("repo", "snap", "a.py", "run", 1, 2, "def run():\n    return 2")

    assert same == same_again
    assert same != changed
