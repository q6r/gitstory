"""File-type detection and the generated-file filter."""

from __future__ import annotations

import pytest

from gitstory import languages
from gitstory.languages import OTHER, is_generated, language_for


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/app.py", "Python"),
        ("src/app.PY", "Python"),  # extensions are case-insensitive
        ("deep/er/main.go", "Go"),
        ("lib/x.h", "C"),
        ("lib/x.hpp", "C++"),
        ("Dockerfile", "Dockerfile"),
        ("build/dockerfile", "Dockerfile"),
        ("Dockerfile.dev", "Dockerfile"),
        ("Containerfile", "Dockerfile"),
        ("Makefile", "Makefile"),
        ("rules.mk", "Makefile"),
        ("CMakeLists.txt", "CMake"),  # a well-known name beats the .txt extension
        ("notes.TXT", "Text"),
        ("Gemfile", "Ruby"),
        ("brkit.gemspec", "Ruby"),
        ("page.html", "HTML"),
        ("data.jsonl", "JSON"),
        ("archive.tar.gz", OTHER),
        ("README", OTHER),
        (".gitignore", OTHER),
        ("", OTHER),
        ("trailing.", OTHER),
    ],
)
def test_language_for(path: str, expected: str) -> None:
    assert language_for(path) == expected


def test_language_for_only_looks_at_the_final_path_component() -> None:
    assert language_for("python.py/README") == OTHER
    assert language_for("dir.go/file") == OTHER
    # git paths use "/" only, so a backslash is an ordinary character of the file name
    assert language_for("dir\\file.py") == "Python"


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "Package-Lock.JSON",
        "web/yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "go.sum",
        "dist/app.min.js",
        "dist/style.min.css",
        "static/app.js.map",
        "api/service_pb2.py",
        "api/service_pb2_grpc.py",
        "api/service.pb.go",
        "Form1.Designer.cs",
        "vendor/lib/x.c",
        "web/node_modules/pkg/index.js",
        "src/third_party/zlib/zlib.c",
        "anything.lock",
    ],
)
def test_generated_files_are_recognised(path: str) -> None:
    assert is_generated(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "vendor",  # a file that happens to be called vendor
        "docs/vendor.md",
        "src/vendorish/x.py",
        "node_modules.md",
        "lock.py",
        "minified.js",
        "app.min.txt",
        "map.txt",
        "",
    ],
)
def test_ordinary_files_are_not_generated(path: str) -> None:
    assert is_generated(path) is False


def test_every_extension_belongs_to_exactly_one_language() -> None:
    seen: dict[str, str] = {}
    for language, extensions in languages._EXTENSIONS_BY_LANGUAGE.items():
        for extension in extensions.split():
            assert extension == extension.lower(), extension
            assert not extension.startswith("."), extension
            assert extension not in seen, (
                f"{extension} is claimed by {seen[extension]} and {language}"
            )
            seen[extension] = language
    assert len(languages._BY_EXTENSION) == len(seen)
