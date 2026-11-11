# Using gitstory

All output below is real: it was produced on the synthetic repository that
[`examples/generate.py`](../examples/generate.py) builds (about 800 commits by 15 authors over six
years), with the clock pinned by `SOURCE_DATE_EPOCH` so that the numbers are reproducible.

- [Install](#install)
- [Commands](#commands)
- [Choosing the history](#choosing-the-history)
- [`summary`](#summary), [`json`](#json), [`report`](#report), [`card`](#card), [`serve`](#serve)
- [Exit codes](#exit-codes)
- [Environment](#environment)
- [Using it as a library](#using-it-as-a-library)

## Install

gitstory needs Python 3.10 or newer and `git` on your `PATH`. It has no other dependencies.
Get the source from [github.com/q6r/gitstory](https://github.com/q6r/gitstory) and, inside the
folder:

```bash
python -m pip install .
gitstory --version
```

```text
gitstory 0.1.0
```

`python -m gitstory ...` works too. To work on gitstory itself, install it editable with the
test tools (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Commands

```text
usage: gitstory [-h] [--version] [--debug] <command> ...

positional arguments:
  <command>
    report    write a self-contained HTML dashboard
    json      print machine-readable metrics (schema gitstory.metrics/v1)
    card      write an animated SVG stats card for a README
    serve     serve the dashboard on a local web server
    summary   print a summary in the terminal

options:
  -h, --help  show this help message and exit
  --version   show program's version number and exit
  --debug     show a traceback on unexpected errors
```

Every command takes an optional `path` (any directory inside the repository, default: the
current one) and the [history filters](#choosing-the-history). `gitstory <command> --help` lists
the options of one command.

## Choosing the history

| Option | Meaning |
|---|---|
| `--since DATE`, `--until DATE` | Only commits in that window (compared with the *committer* date, inclusive). `DATE` is `YYYY-MM-DD`, an ISO-8601 timestamp (`2024-06-30T12:00:00+02:00`) or a relative value: `90d`, `12w`, `6m`, `1y`. A date without a time means the start of that day (the end of it for `--until`). Values without an offset are UTC. |
| `--author REGEX` | Only commits whose author name or e-mail matches the regular expression (case-insensitive, git syntax). |
| `--branch REV` | A branch, tag or commit to analyse (default: `HEAD`). Ranges such as `a..b` and option-like values are refused. |
| `--path PATHSPEC` | Only commits touching that path, relative to the repository root. Repeat it for several paths. |
| `--no-merges` | Leave merge commits out. |
| `--max-commits N` | Analyse at most the `N` most recent matching commits. |

Dates are validated before git is started: git's own parser accepts almost any text silently.
A filter that matches nothing is not an error; the output says so (see [`json`](#json)).

```bash
gitstory summary --since 2024-01-01 --until 2024-12-31 --author ada
```

```text
gitstory · story-repo  (main @ 1e8b39c5fb52)
████████████████████████████████████████████████████████████████████████
42 commits  ·  1 author  ·  41 active days  ·  +527 / -407 lines
2024-01-11 → 2024-12-15  (11m)

Activity
  last 52 weeks  ▄▄▄▁▄▆▄▄▁▁▄▄▄▁▄▄▁▁▄▁▁▄▆█▄▆▁▁▁▄▁▄▁▁▄▄▆▄▁▁█▆▁▄█▄▁▄▄▁▁▁
  streak       current 0 days, longest 2 days (2024-02-11 → 2024-02-12)
  chronotype   Evening coder
               early bird 0%, nine-to-five 0%, evening coder 38%, night owl 62%
```

(The chronotype is chosen by share relative to the width of each window, which is why 38% of the
commits in four evening hours beat 62% in seven night hours; see
[docs/metrics.md](metrics.md#chronotype).)

## summary

Prints a compact overview in the terminal: sparklines, a weekday x hour grid, bars for authors
and languages, hot files, commit-message habits and records.

```bash
gitstory summary
```

```text
gitstory · story-repo  (main @ 1e8b39c5fb52)
████████████████████████████████████████████████████████████████████████
826 commits  ·  15 authors  ·  639 active days  ·  +16,379 / -4,619 lines
2021-01-05 → 2026-11-09  (5y 10m)

Activity
  last 52 weeks  ▄▃▂▁▄▃▄▃▃█▃▄▃▂▄▃▃▃▄▃▃▄▁▅▃▃▄▃▂▃▃▃▃▃▂▃▃▂▁▃▂▂▃▁▃▁▂▄▂▂▃▂
  streak       current 4 days, longest 21 days (2024-02-05 → 2024-02-25)
  chronotype   Nine-to-five
               early bird 12%, nine-to-five 54%, evening coder 15%, night owl 19%

Week x hour (local time)
       0     6     12    18
  Mon  ▒··▒··▒▒▒▓▓▒··▓▓▒░·▒▒▒░▓
  Tue  ░··▒··░░░▒█░·░▓▒▒▓░▒▒▒░░
  ...
```

The full output is in [`examples/summary.txt`](../examples/summary.txt) and as a picture in the
[README](../README.md#usage).

| Option | Meaning |
|---|---|
| `--color auto\|always\|never` | When to use colour. `auto` colours a terminal only, and honours `NO_COLOR` and `TERM=dumb`. |

The layout is built for a terminal at least 80 columns wide (it uses up to 110). When the
terminal cannot encode the block characters, the bars fall back to ASCII (`#` and `-`), and
characters the terminal cannot show at all (say, an author name in Chinese on a legacy Windows
code page) are replaced by `?` instead of stopping the program. Names that come from the
repository are cleaned first: control and bidirectional-override characters are shown as visible
placeholders, so a hostile author name cannot send escape sequences to your terminal.

## json

Prints the [metrics document](metrics.md) (schema `gitstory.metrics/v1`) as UTF-8 JSON.

```bash
gitstory json
```

```text
{
  "schema": "gitstory.metrics/v1",
  "generator": {
    "name": "gitstory",
    "version": "0.1.0"
  },
  "generatedAt": "2026-11-10T12:00:00Z",
  "repository": {
    "name": "story-repo",
    "branch": "main",
    "detached": false,
    "head": "1e8b39c5fb52",
    "shallow": false
  },
  "filters": {
    "since": null,
    "until": null,
    "author": null,
    "branch": null,
    "paths": [],
    "noMerges": false,
    "maxCommits": null
  },
  "warnings": [],
  "totals": {
    "commits": 826,
    "mergeCommits": 45,
    "authors": 15,
    "additions": 16379,
    "deletions": 4619,
    "filesChanged": 1347,
    "binaryChanges": 2,
    "activeDays": 639,
    "firstCommitAt": "2021-01-05T00:05:09Z",
```

| Option | Meaning |
|---|---|
| `-o, --output FILE` | Write to a file instead of standard output. |
| `--compact` | No indentation, one line. |

Warnings (a shallow clone, unparseable git output, ...) go to **standard error**, so the JSON on
standard output is always valid. When nothing matches the filters the command still succeeds
and prints an empty document:

```bash
gitstory json --since 2031-01-01 --compact
```

```text
gitstory: warning: No commits matched the given filters.
{"schema":"gitstory.metrics/v1","generator":{"name":"gitstory","version":"0.1.0"},...
```

## report

Writes one self-contained HTML file: the Aurora UI kit, the dashboard code and the data
are all inside it, so it works offline and from `file://`. Open it in any modern browser.

```bash
gitstory report -o story.html
```

```text
gitstory: wrote story.html (346,992 bytes)
```

| Option | Meaning |
|---|---|
| `-o, --output FILE` | File to write (default `gitstory.html`; `-` for standard output). |
| `--theme auto\|dark\|light` | `auto` follows the system setting and the toggle in the page; `dark` and `light` fix the theme the page opens in. |

The page has a story section (the headline numbers and highlight cards), the activity calendar,
commits over time (monthly, weekly or yearly), a weekday x hour heatmap, authors and the bus
factor, hot files, languages and commit sizes, commit types and words, releases, and a preview
of the [README card](#card).

The page is laid out like an app. The sections are the icons of a rail on the left (a bar at the
bottom of a phone) and the rail follows the scroll. The top bar has the breadcrumbs, a search
button that opens the command palette (`Ctrl`+`K`) and the theme toggle; the bar under it has the
*Charts / Tables* switch, which turns every chart into its table, and the facts of the repository
(branch, commits, authors, time span, and a *Filtered* pill when you chose a history with filters).
Every chart also has its own *Table* view, the page works with the keyboard and respects reduced
motion. Without JavaScript it shows the headline numbers only.

## card

Writes an animated SVG card for a README: your commits, active days and current streak, a
20-week heatmap and a language bar. It is pure SVG and CSS (no script, no images, no external
requests), so GitHub renders it inside an `<img>`.

```bash
gitstory card -o card.svg --theme dark
```

```text
gitstory: wrote card.svg (18,612 bytes)
```

Reference it from Markdown:

```markdown
![gitstory card](card.svg)
```

| Option | Meaning |
|---|---|
| `-o, --output FILE` | File to write (default `card.svg`; `-` for standard output). |
| `--theme auto\|dark\|light` | `auto` (the default) follows the viewer's colour scheme inside the SVG itself. |
| `--width`, `--height` | Size in pixels (320 to 1200, 160 to 600; default 495 x 195). |
| `--weeks N` | Weeks in the heatmap (8 to 53, default 20). A narrow card shows fewer when they would not fit. |
| `--title TEXT` | A title instead of the repository name. |

The animation switches off under `prefers-reduced-motion`. See [`examples/`](../examples) for
the three themes.

## serve

Serves the same dashboard from your machine, so you can keep it open while you work.

```bash
gitstory serve --port 17320
```

```text
gitstory: analysing . ...
gitstory: serving http://127.0.0.1:17320/ (Ctrl+C to stop)
```

| Option | Meaning |
|---|---|
| `--host HOST` | Address to bind. Loopback only (`127.0.0.1`, `localhost`, `::1`) unless you pass `--allow-remote`. |
| `--port PORT` | Port to listen on (default 8787; `0` picks a free one). |
| `--open` | Open the dashboard in your browser. |
| `--theme auto\|dark\|light` | As for `report`. |
| `--allow-remote` | Allow a non-loopback address. **There is no authentication**: anyone who can reach the port can read your repository's data. |
| `-v, --verbose` | Log every request on standard error. |

The page served here has a *Live* indicator and a refresh button in the top bar; the button asks
the server to read the repository again, and `Ctrl`+`K` has the same command.

| Route | Answer |
|---|---|
| `GET /` | The dashboard (what `report` writes). |
| `GET /api/metrics.json` | The [metrics document](metrics.md). |
| `GET /card.svg` | The card. `?theme=auto\|dark\|light` picks the colours. |
| `POST /api/refresh` | Re-read the repository. Same-origin requests only, at most once per second. Answers `{"ok": true, "refreshed": ..., "generatedAt": ..., "commits": ..., "etag": ...}`. |
| `GET /favicon.ico` | `204 No Content`. |

Everything else is `404` (there is no static-file handler: no URL ever touches the file system),
every method but `GET`/`HEAD` (and `POST` to the refresh route) is `405`, and `ETag`/`If-None-Match`
are supported. The data is held in memory; the repository is only read at start-up and when you
refresh.

```bash
curl -s -i "http://127.0.0.1:17320/card.svg?theme=dark"
```

```text
HTTP/1.1 200 OK
Server: gitstory
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox; frame-ancestors 'none'
Cache-Control: no-cache
ETag: "b9a0dd5851965a49fed1a825"
Content-Type: image/svg+xml; charset=utf-8
Content-Length: 18546
```

```bash
curl -s -X POST http://127.0.0.1:17320/api/refresh
```

```text
{"ok": true, "refreshed": true, "generatedAt": "2026-11-10T12:00:00Z", "commits": 826, "etag": "\"7ce5432c36b32d97eb89d1a2\""}
```

A request whose `Host` header is not the server's own address is refused, which is what stops a
web page on another site from reading your data through DNS rebinding:

```bash
curl -s -H "Host: evil.example" http://127.0.0.1:17320/
```

```text
{"error": "host not allowed"}
```

See [Security notes](../README.md#security-notes) for the rest of the model.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success. |
| 1 | Unexpected failure. Run with `--debug` for a traceback. |
| 2 | Invalid usage: an unknown option, a bad value, a bad date, a refused address. |
| 3 | `git` was not found on `PATH`. |
| 4 | Not a git repository, or the requested branch or revision does not exist. |
| 5 | The repository has no commits yet. |
| 6 | git failed or timed out while reading the history. |
| 7 | The output could not be written (or the server could not listen). |
| 130 | Interrupted (`Ctrl`+`C`). |

```bash
cd /tmp && gitstory json; echo "exit code: $?"
```

```text
gitstory: error: . is not inside a git repository (run gitstory in a clone, or pass the path of one)
exit code: 4
```

## Environment

| Variable | Effect |
|---|---|
| `SOURCE_DATE_EPOCH` | Pins the clock (seconds since 1970). The same repository then always gives byte-identical output; this is how [`examples/`](../examples) are made. |
| `NO_COLOR` | Disables colour in `summary` when it is not forced with `--color always`. |
| `COLUMNS` | The terminal width `summary` lays itself out for (80 to 110). |

gitstory runs `git` with its own scrubbed environment: no pager, no prompts, and the variables
that select a repository (`GIT_DIR`, `GIT_WORK_TREE`, ...) are not passed on.

## Using it as a library

The command line is a thin layer over three functions. The metrics document is a plain `dict`.

```python
from gitstory.collect import collect_history
from gitstory.metrics import compute_metrics
from gitstory.model import Filters
from gitstory.util import utc_now

history = collect_history("path/to/repo", Filters(no_merges=True, max_commits=5_000))
metrics = compute_metrics(history, now=utc_now())
print(metrics["totals"]["commits"], metrics["busFactor"]["value"])
```

[`examples/use_as_a_library.py`](../examples/use_as_a_library.py) is a runnable version:

```bash
python examples/use_as_a_library.py path/to/repo
```

```text
story-repo: 781 commits by 15 authors
longest streak: 21 days, bus factor: 3
- Commits: 781 (from 2021-01-05 to 2026-11-09, by 15 authors)
- Active days: 622 (29% of the days in the range had a commit)
- Longest streak: 21 (days in a row, from 2024-02-05 to 2024-02-25)
- Current streak: 0 (the last commit was 2 days ago)
- Busiest day: 17 (commits on 2024-09-12)
```

(That run used the real clock, two days after the last commit of the example repository. The
renderers are public too: `gitstory.render.html.render_report`, `gitstory.render.svg_card.render_card`
and `gitstory.render.terminal.render_summary` take the metrics document and return text.)
