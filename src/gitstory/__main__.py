"""Allow ``python -m gitstory``."""

from __future__ import annotations

from gitstory.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
