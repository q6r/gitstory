"""File-type detection for the language breakdown, plus a filter for generated files.

The map is deliberately plain: a lower-cased extension (or a well-known file name) to a
display name. Nothing is read from disk and nothing is guessed from file contents.
"""

from __future__ import annotations

from pathlib import PurePosixPath

OTHER = "Other"

_EXTENSIONS_BY_LANGUAGE = {
    "Python": "py pyi pyw",
    "JavaScript": "js mjs cjs jsx",
    "TypeScript": "ts mts cts tsx",
    "Go": "go",
    "Rust": "rs",
    "Java": "java",
    "Kotlin": "kt kts",
    "Scala": "scala sc",
    "Swift": "swift",
    "Objective-C": "m mm",
    "C": "c h",
    "C++": "cc cpp cxx hpp hh hxx ino",
    "C#": "cs csx",
    "F#": "fs fsx",
    "Visual Basic": "vb",
    "PHP": "php phtml",
    "Ruby": "rb rake gemspec",
    "Perl": "pl pm",
    "Lua": "lua",
    "R": "r rmd",
    "Julia": "jl",
    "Dart": "dart",
    "Elixir": "ex exs",
    "Erlang": "erl hrl",
    "Haskell": "hs lhs",
    "OCaml": "ml mli",
    "Clojure": "clj cljs cljc edn",
    "Lisp": "lisp el scm rkt",
    "Zig": "zig",
    "Nim": "nim",
    "Crystal": "cr",
    "Shell": "sh bash zsh fish ksh",
    "PowerShell": "ps1 psm1 psd1",
    "Batch": "bat cmd",
    "HTML": "html htm xhtml",
    "CSS": "css scss sass less styl",
    "Vue": "vue",
    "Svelte": "svelte",
    "Astro": "astro",
    "SQL": "sql psql",
    "Markdown": "md mdx markdown",
    "reStructuredText": "rst",
    "AsciiDoc": "adoc asciidoc",
    "Text": "txt",
    "TeX": "tex sty bib",
    "JSON": "json jsonc json5 jsonl ipynb",
    "YAML": "yml yaml",
    "TOML": "toml",
    "XML": "xml xsd xsl xslt plist",
    "SVG": "svg",
    "INI": "ini cfg conf properties",
    "GraphQL": "graphql gql",
    "Protocol Buffers": "proto",
    "Terraform": "tf tfvars hcl",
    "Nix": "nix",
    "Assembly": "asm s nasm",
    "Vim script": "vim",
    "Solidity": "sol",
    "GLSL": "glsl vert frag",
    "Makefile": "mk mak",
    "CMake": "cmake",
}


def _extension_map() -> dict[str, str]:
    return {
        f".{extension}": language
        for language, extensions in _EXTENSIONS_BY_LANGUAGE.items()
        for extension in extensions.split()
    }


_BY_EXTENSION = _extension_map()

_BY_FILENAME: dict[str, str] = {
    "dockerfile": "Dockerfile",
    "containerfile": "Dockerfile",
    "makefile": "Makefile",
    "gnumakefile": "Makefile",
    "cmakelists.txt": "CMake",
    "gemfile": "Ruby",
    "rakefile": "Ruby",
    "jenkinsfile": "Groovy",
    "justfile": "Just",
    "vagrantfile": "Ruby",
    "procfile": "Shell",
}

_GENERATED_NAMES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "deno.lock",
        "cargo.lock",
        "poetry.lock",
        "uv.lock",
        "pipfile.lock",
        "pdm.lock",
        "gemfile.lock",
        "composer.lock",
        "go.sum",
        "flake.lock",
        "mix.lock",
        "pubspec.lock",
        "podfile.lock",
        "packages.lock.json",
        "gradle.lockfile",
    }
)
_GENERATED_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".pb.go",
    "_pb2.py",
    "_pb2_grpc.py",
    ".designer.cs",
)
_VENDORED_DIRECTORIES = frozenset({"node_modules", "vendor", "third_party"})


def language_for(path: str) -> str:
    """Return the display language of ``path``, or ``"Other"`` when it is not recognised."""
    name = PurePosixPath(path).name.lower()
    named = _BY_FILENAME.get(name)
    if named is not None:
        return named
    if name.startswith("dockerfile."):
        return "Dockerfile"
    suffix = PurePosixPath(name).suffix
    return _BY_EXTENSION.get(suffix, OTHER)


def is_generated(path: str) -> bool:
    """True for lockfiles, minified bundles, source maps and vendored code.

    These dominate churn without telling anything about the project's own work, so they are
    left out of the hotspot and language rankings (commit sizes still count every line).
    """
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in _GENERATED_NAMES or name.endswith(_GENERATED_SUFFIXES):
        return True
    return any(part in _VENDORED_DIRECTORIES for part in pure.parts[:-1])
