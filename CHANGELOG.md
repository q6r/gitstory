# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The dashboard moved to the Aurora UI kit 2.0**: the Aurora layout in the Prism Classic palette
  (Prism Daylight in the light theme). A rail with the sections, a top bar with breadcrumbs,
  search and the theme toggle, a sub bar with a Charts/Tables switch and the facts of the
  repository, flat cards and a status bar replace the top navigation, glass cards and gradients.
  `gitstory serve` shows its *Live* indicator and refresh button in the top bar. Every chart still
  has its Table view; the new Charts/Tables switch flips all of them at once.
- **The animated SVG card follows the same look**: a near-black ground (white in the light theme),
  silver ink, one indigo accent, and language colours in a fixed order. Its constraints are the
  same: pure SVG and CSS, no script, no external references.

## [0.1.0] - 2026-11-10

First release.

### Added

- **Collector**: streams `git log -z --numstat` incrementally with a fixed argv, a scrubbed
  environment and bounded sizes; follows renames, honours `.mailmap`, reads tags; filters by
  date (`--since`, `--until`), author, revision, path, merges and count.
- **Metrics** (`gitstory.metrics/v1`): totals, activity by day, week, month and year, a 53-week
  calendar, a weekday x hour matrix, streaks, authors with a bus-factor heuristic, churn
  hotspots, languages, commit-size distribution, Conventional Commits mix, top words,
  chronotype, records, release cadence and highlights. Documented in `docs/metrics.md`.
- **`gitstory summary`**: a terminal summary with sparklines and bars; colour follows
  `NO_COLOR`, the terminal and `--color`; falls back to ASCII; never lets repository text send
  escape sequences.
- **`gitstory json`**: the metrics document, pretty or compact, to stdout or a file.
- **`gitstory report`**: one self-contained, offline HTML dashboard (Aurora UI kit inlined,
  JSON data block escaped, hash-based content-security policy): story and highlight cards,
  activity calendar, commits over time, weekday x hour heatmap, authors, hot files, languages,
  commit sizes, commit types, words, releases and a README card preview; Chart/Table views,
  command palette, dark and light themes, keyboard and reduced-motion support.
- **`gitstory card`**: an animated SVG stats card for READMEs (pure SVG and CSS, renders inside
  GitHub's image sandbox, follows the viewer's colour scheme).
- **`gitstory serve`**: the dashboard, `/api/metrics.json` and `/card.svg` on loopback only;
  Host-header check against DNS rebinding, a fixed route table that never touches the file system,
  same-origin refresh, strict security headers, bounded requests, graceful shutdown.
- Exit codes for every failure class, `SOURCE_DATE_EPOCH` for reproducible output, a typed
  package (`py.typed`) and the `gitstory` entry point.
- A deterministic synthetic repository with an independent oracle, a hostile-input repository,
  and a test suite of about a thousand tests (offline, about a minute), with seeded property-style
  tests for the parsers and sanitisers.
- Documentation (`docs/`), regenerable examples (`examples/`), screenshots and an animated
  banner.

[Unreleased]: https://github.com/q6r/gitstory/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/q6r/gitstory/releases/tag/v0.1.0
