"""Regenerate the sample outputs in this folder from the synthetic repository.

    python examples/generate.py            # writes next to this script
    python examples/generate.py --out /tmp/gitstory-demo

The repository is built by ``tests/fixtures/make_repo.py`` (about 800 commits by a dozen authors
over six years, fully deterministic) and analysed at a fixed clock, so running this script twice
gives byte-identical files. It uses the same public functions as the command line:

* ``collect_history``  reads the history from git,
* ``compute_metrics``  turns it into the ``gitstory.metrics/v1`` document,
* ``render_report`` / ``render_card`` / ``render_summary`` draw it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "src"))

from make_repo import NOW, build_synthetic_repo  # noqa: E402

from gitstory.collect import collect_history  # noqa: E402
from gitstory.metrics import compute_metrics  # noqa: E402
from gitstory.render.html import render_report  # noqa: E402
from gitstory.render.svg_card import CardOptions, render_card  # noqa: E402
from gitstory.render.terminal import TermStyle, render_summary  # noqa: E402
from gitstory.util import to_json  # noqa: E402


def write(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 with LF line endings and report it."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"wrote {path.name} ({path.stat().st_size:,} bytes)")


def main() -> None:
    """Build the synthetic repository in a temporary folder and write every sample."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)

    # TemporaryDirectory also removes git's read-only object files, which rmtree cannot on Windows
    with tempfile.TemporaryDirectory(prefix="gitstory-examples-") as scratch:
        repo = build_synthetic_repo(Path(scratch) / "story-repo").path
        metrics = compute_metrics(collect_history(repo), now=NOW)

    write(out / "metrics.json", to_json(metrics, indent=2) + "\n")
    write(out / "report.html", render_report(metrics))
    for theme in ("auto", "dark", "light"):
        name = "card.svg" if theme == "auto" else f"card-{theme}.svg"
        write(out / name, render_card(metrics, CardOptions(theme=theme)))
    write(
        out / "summary.txt",
        render_summary(metrics, TermStyle(color=False, unicode=True, width=100)),
    )


if __name__ == "__main__":
    main()
