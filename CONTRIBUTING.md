# Contributing to gitstory

Thank you for helping. gitstory is small on purpose: standard library only at runtime, one
direction of data flow (read, reduce, render) and tests for every behaviour. Please keep it that
way. Read [docs/architecture.md](docs/architecture.md) first; it is short.

## Set up

You need Python 3.10 or newer and `git`.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Run these inside the virtual environment (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere). The `dev` extra adds pytest, coverage, ruff and mypy; the
runtime has no dependencies and must stay that way.

## Run the checks

```bash
python -m pytest                          # the whole suite, offline, about a minute
python -m ruff check .                    # lint
python -m ruff format --check .           # formatting (drop --check to apply it)
python -m mypy                            # strict type checking of src/
python -m coverage run -m pytest && python -m coverage report
```

CI runs the same on Linux, macOS and Windows with Python 3.10 to 3.14, so avoid anything that
needs a newer interpreter or a particular operating system. Tests that open a port use
`127.0.0.1` in 17300 to 17399 (see `tests/conftest.py`).

## Where things go

| You are changing | Look at | Add tests in |
|---|---|---|
| How history is read or parsed | `src/gitstory/collect.py` | `tests/test_collect_*.py` |
| A number in the report | `src/gitstory/metrics/` | `tests/test_metrics_*.py` (and say what it means in [docs/metrics.md](docs/metrics.md)) |
| The terminal summary, the card or the HTML shell | `src/gitstory/render/` | `tests/test_render_*.py` |
| The dashboard | `src/gitstory/assets/app.js`, `app.css`, `report.html` (and the `aurora*` kit files beside them, if the kit itself must change) | `tests/test_dashboard_assets.py`, and look at it in a browser |
| The local server | `src/gitstory/serve.py` | `tests/test_serve.py` |
| Options and exit codes | `src/gitstory/cli.py` | `tests/test_cli.py` and [docs/usage.md](docs/usage.md) |

Some rules that the tests enforce and a review will check:

- **Repository content is untrusted.** Author names, subjects, paths and tags can contain
  anything. Never build HTML, SVG, shell commands or file paths from them; use the existing
  escaping and sanitising helpers. A change that puts repository text somewhere new needs a
  hostile-input test.
- **Outputs are deterministic.** No clock, randomness or dict-ordering surprises; take `now` as a
  parameter. The same repository must give the same bytes.
- **Everything is bounded.** New data from git needs a size limit.
- **The dashboard stays offline.** No CDN, no web fonts, no `innerHTML`, no inline handlers; it
  has to work under the content-security policy that `render/html.py` writes.
- **The Aurora UI kit is part of this repository.** `aurora.css`, `aurora.js` and
  `aurora-charts.js` in `src/gitstory/assets/` are small, dependency-free files (MIT) that the
  dashboard is built on. Project-specific styling and behaviour belong in `app.css` and `app.js`;
  the kit files may be edited with care. Keep their rules: no `innerHTML`, no inline `style`
  attributes or scripts, colours only from the tokens in `aurora.css`, no external requests.

## Looking at the dashboard

The generated examples are the quickest way to see a change:

```bash
python examples/generate.py
```

then open `examples/report.html` (and `examples/card.svg`) in a browser. The script rebuilds
the synthetic repository from `tests/fixtures/make_repo.py`, so the numbers never change.
Commit the regenerated files with the change that caused them.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, `perf:`, `build:`, `ci:`, `chore:`. A scope helps (`fix(serve): ...`),
  and `!` marks a breaking change.
- Keep pull requests focused, and describe what you ran. Add an entry under *Unreleased* in
  [CHANGELOG.md](CHANGELOG.md) for anything a user can notice.
- Found a security problem? Please follow [SECURITY.md](SECURITY.md) instead of opening an
  issue.

By contributing you agree that your work is released under the [MIT licence](LICENSE).
