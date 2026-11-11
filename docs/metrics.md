# Metrics reference

Every number gitstory shows comes from one JSON document with the schema id
`gitstory.metrics/v1`. `gitstory json` prints it, `gitstory report` embeds it in the HTML page
and `gitstory serve` serves it at `/api/metrics.json`. This page says what each field means and
exactly how it is computed. A real, complete document is in
[`examples/metrics.json`](../examples/metrics.json).

- [Conventions](#conventions)
- [The document at a glance](#the-document-at-a-glance)
- [Sections](#sections): [totals](#totals), [activity](#activity), [streaks](#streaks),
  [authors](#authors), [busFactor](#busfactor), [hotspots](#hotspots), [languages](#languages),
  [commitSize](#commitsize), [conventional](#conventional), [words](#words),
  [chronotype](#chronotype), [records](#records), [releases](#releases),
  [highlights](#highlights), [warnings](#warnings)
- [Limits](#limits)
- [Things a number cannot tell you](#things-a-number-cannot-tell-you)

## Conventions

- **One pass.** The history is read once, newest commit first, and fed to small accumulators.
  Memory does not grow with the number of commits, only with the number of distinct days, authors
  and files.
- **Author-local time.** A commit belongs to the calendar day, weekday and hour of the clock *it
  was written on* (the UTC offset recorded in the commit). A commit made at 23:30 in UTC-03:00
  counts for that evening and for the 23:00 column, not for the next UTC day. Absolute instants
  (`firstCommitAt`, `lastCommitAt`, `generatedAt`, tag dates) are UTC and end in `Z`.
- **Merge commits** carry no diff in `git log`, so they have no line statistics. They count as
  commits (and as active days) but are left out of the commit-size distribution and the commit
  message statistics.
- **"Lines changed"** is lines added plus lines deleted, as `git log --numstat` reports them.
  Binary files have no line counts.
- **Deterministic.** The same repository and the same clock always give the same bytes: keys keep
  a fixed order, every list has a total order and ties are broken explicitly. Set
  `SOURCE_DATE_EPOCH` (seconds) to pin the clock, as the examples do.
- **No e-mail addresses.** They are used to tell authors apart and never appear in the output.
- **Filters** (`--since`, `--until`, `--author`, `--branch`, `--path`, `--no-merges`,
  `--max-commits`) are applied by git before anything is counted and are echoed in `filters`.
  `--until` also becomes the "as of" moment, so streaks and the calendar describe that window.

## The document at a glance

```text
schema         "gitstory.metrics/v1"
generator      { name, version }
generatedAt    "2026-11-10T12:00:00Z"
repository     { name, branch, detached, head, shallow }
filters        { since, until, author, branch, paths, noMerges, maxCommits }
warnings       [ "..." ]
totals         counts, lines, the active-day facts
activity       byDay, byWeek, byMonth, byYear, weekdayHour, calendar
streaks        current and longest run of active days
authors        the leaderboard (top 7 and "Other")
busFactor      the smallest group that wrote half of the commits (a heuristic)
hotspots       the files with the most changed lines
languages      changed lines by file type
commitSize     the distribution of commit sizes
conventional   the Conventional Commits mix
words          the most common words in commit subjects
chronotype     when in the day the commits happen
records        biggest commit, busiest day, longest quiet gap
releases       tags: cadence and a list
highlights     the headline facts, derived from everything above
```

## Sections

### totals

| Field | Meaning |
|---|---|
| `commits` | Commits in the analysed range, merges included. |
| `mergeCommits` | Commits with more than one parent. |
| `authors` | Distinct authors (see [authors](#authors) for how identities are merged). |
| `additions`, `deletions` | Lines added and deleted over all commits and **all** files, lockfiles and bundles included. |
| `filesChanged` | The number of (commit, file) entries: a file changed in ten commits counts ten times. |
| `binaryChanges` | How many of those entries were binary files (they have no line counts). |
| `activeDays` | Calendar days (author-local) with at least one commit. |
| `firstCommitAt`, `lastCommitAt` | The earliest and the latest commit instant, UTC. |
| `spanDays` | Whole days from the first to the last commit, both ends included (`0` without commits). |
| `commitsPerActiveDay` | `commits / activeDays`, two decimals. |

### activity

| Field | Meaning |
|---|---|
| `timezone` | Always `"author-local"` (see [Conventions](#conventions)). |
| `asOf` | The day "today" means: the UTC date of the clock (or of `--until`), moved one day forward when the newest commit's local date is already tomorrow (an author east of UTC committing in their morning). |
| `byDay` | `[{date, commits}]`, only days with commits, in date order. |
| `byWeek` | `[{week, start, commits}]`, ISO weeks starting on Monday (`2026-W45`), **dense**: weeks without commits are present with `0`, from the first active week to the current one. At most 1,040 weeks (twenty years). |
| `byMonth` | `[{month, commits}]` (`2026-11`), dense, at most 480 months. |
| `byYear` | `[{year, commits}]`, dense. |
| `weekdayHour` | `{weekdays, matrix, max}`: `matrix[weekday][hour]` is the number of commits (Monday is row 0, hour 0 is midnight); `max` is the largest cell. |
| `calendar` | The last 53 weeks as a Monday-first grid ending on `asOf`: `{weeks, weekStart, from, to, total, max, days}`, one `{date, commits, level}` per day. |

`level` is a heat level from 0 to 5. Days without commits are level 0. The non-empty days are
split into five groups by quantile (the cut-offs are the 20th, 40th, 60th and 80th percentile of
the non-empty days), so a repository with one 40-commit day among many one-commit days still uses
the whole colour ramp.

### streaks

A *streak* is a run of consecutive active days.

| Field | Meaning |
|---|---|
| `current` | Length of the run that ends today **or yesterday**. A streak is not broken until a whole day has passed without a commit, and the day of slack also absorbs timezone skew. `0` when the last commit is older. |
| `currentFrom` | First day of the current run (`null` when `current` is `0`). |
| `longest`, `longestFrom`, `longestTo` | The longest run ever; ties go to the earliest. |
| `daysSinceLastCommit` | `today - last active day`, never negative. |

Commits dated more than one day after "today" (a wrong clock on the author's machine) are
ignored here.

### authors

`{total, limit, items}`: `total` counts every distinct author, `items` holds the top `limit`
(7), followed by one folded row for everyone else.

An author is identified by the **mailmap-resolved e-mail address, case-folded**, so two names
used with the same address are one person, and a `.mailmap` merges identities that used several
addresses. Without an address the case-folded name is used. The name shown is the one used most
often with that address (ties: alphabetically first).

Each item: `name`, `commits`, `additions`, `deletions`, `activeDays` (author-local days),
`firstCommitAt`, `lastCommitAt`, `share` (of all commits, 4 decimals), `members` (how many people
a row stands for: `1`, or the size of the fold) and `other` (`true` for the folded row, which is
named `"Other"`).

Ranking: most commits first, then most changed lines, then name (case-insensitive).

### busFactor

`{value, threshold, coverage, authors, heuristic, note}`. `value` is the **smallest number of
authors whose commits add up to at least half of all commits** (`threshold` is `0.5`),
`coverage` is the share they actually cover and `authors` names them. It is a rough heuristic
and says nothing about who knows the code or who reviews it; the document says so in `note`.

### hotspots

`{basis, minCommits, uniqueFiles, ignoredChanges, files}`: the 15 files with the most changed
lines.

- `files` items: `path`, `commits`, `additions`, `deletions`, `changes`, `share` (of all
  tracked changed lines), `language`.
- **Renames are followed.** The log is read newest first, so a rename seen at commit C tells us
  that older commits touching the old path belong to the new path: a file that moved around is
  still one hotspot, under its newest name. This is best effort for non-linear histories.
- **Generated files are left out**: lockfiles (`package-lock.json`, `poetry.lock`, `go.sum`,
  ...), minified bundles and source maps (`*.min.js`, `*.map`), generated code (`*_pb2.py`,
  `*.pb.go`) and vendored directories (`node_modules`, `vendor`, `third_party`).
  `ignoredChanges` says how many changes that was.
- **A hotspot is a file that keeps changing**, so files touched by a single commit (a data dump,
  a vendored import) are not ranked, unless no file was touched twice (`minCommits` is then
  `1`). Otherwise `minCommits` is `2`.
- Tracking stops at 500,000 distinct files; later files still count towards `languages` and a
  warning says so.

### languages

`{basis, total, items}`: changed lines by file type, the top 6 plus an `Other` row
(`other: true`) that also holds every file whose type is not recognised.

The language of a change is decided by the file's name **at the time of that commit**: the file
name (`Dockerfile`, `Makefile`, `CMakeLists.txt`, ...) or the lower-cased extension, from a plain
table in [`languages.py`](../src/gitstory/languages.py). Nothing is read from disk and nothing is
guessed from contents. Generated files are left out, as for `hotspots`.

### commitSize

`{unit, commits, mean, median, p90, max, buckets}` over the non-merge commits; the size of a
commit is its changed lines over all files.

- `median` and `p90` use the **nearest-rank** method (the value at rank `ceil(p * n)`), so they
  are always sizes that really occurred.
- `buckets` are decades: `0`, `1-9`, `10-99`, `100-999`, `1000-9999` and `10000+`, each
  `{id, min, max, commits}` (`max` is `null` for the last one). They are computed with integer
  arithmetic (the number of digits), not a floating-point logarithm.

### conventional

`{considered, typed, share, breaking, types}` reads the **subject line only** of every non-merge
commit and checks it against `type(scope)!: description`.

- A subject is *typed* when it matches that shape. The type is lower-cased.
- The known types are `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`,
  `chore` and `revert`. Any other well-formed prefix counts as `other`; everything else as
  `untyped`.
- `breaking` counts the subjects with a `!` before the colon.
- `types` lists the known types by count (ties alphabetically), then `other`, then `untyped`;
  rows with no commits are left out.

### words

`{tokens, unique, top}`: the 30 most frequent words in commit subjects.

- A word is a run of letters (digits and underscores split words), at least 3 and at most 24
  characters, case-folded. Inner apostrophes are kept (`maintainer's`).
- English and Portuguese stopwords are dropped (`the`, `and`, `o`, `da`, ...). Verbs that
  characterise a commit style (`add`, `fix`, `update`) are kept on purpose: they are what the
  list is meant to show.
- The Conventional Commits prefix is not part of the words.
- There is **no word segmentation**: a run of ideographs is one token, so a two-character Chinese
  word is below the minimum length and a longer phrase is kept whole.

### chronotype

`{type, label, total, peakHour, hours, windows}` says when in the day the commits happen, by the
author's local hour.

Four windows partition the day:

| id | label | hours |
|---|---|---|
| `early-bird` | Early bird | 05:00 to 08:59 |
| `nine-to-five` | Nine-to-five | 09:00 to 17:59 |
| `evening` | Evening coder | 18:00 to 21:59 |
| `night-owl` | Night owl | 22:00 to 04:59 |

`windows` reports `commits` and `share` for each. The **label is not simply the window with the
most commits**: a window that merely covers more hours would win by default. The winner is the
window with the highest share *relative to its width* (share divided by hours/24, a "lift"); ties
go to the earlier window. For example, 38% of the commits in the four evening hours beat 62% in
the seven night hours. With fewer than 10 commits there is no label (`type` is `null`, `label` is
`"Not enough data"`). `peakHour` is the busiest local hour (ties: the earliest).

### records

- `biggestCommit`: `{sha, subject, author, date, additions, deletions, files}` for the commit
  with the most changed lines (ties: the earliest, then the smallest sha). `sha` is 12 characters,
  `subject` is cut to 120.
- `busiestDay`: `{date, commits}`, ties to the earliest day.
- `longestGap`: `{days, from, to}`, the longest run of days **without** a commit between two
  active days (`from` and `to` are the active days around it); ties to the earliest. `null` when
  there is no quiet day at all.

### releases

Every tag reachable from the analysed revision is a release. Annotated tags use the tagger date,
lightweight tags the date of the commit they point at (at most 2,000 tags are read).

`{count, annotated, meanIntervalDays, medianIntervalDays, latest, perYear, items}`; intervals are
days between consecutive tags, rounded to a tenth, in UTC order. `latest` is
`{name, date, daysAgo}` (never negative). `perYear` is `[{year, releases}]` by UTC year. `items`
holds the **latest 50** tags as `{name, date, annotated, daysSincePrevious}` (the first tag has
no previous one: `null`).

### highlights

A short, ordered list of headline facts, each `{id, title, value, caption}`. `value` is a number
when the fact is a count (so a page can animate it) and text otherwise. A highlight whose data is
missing is left out. The ids, in order:

`commits`, `activeDays`, `longestStreak`, `currentStreak`, `busiestDay`, `chronotype`,
`topAuthor`, `busFactor`, `biggestCommit`, `topLanguage`, `hotFile`, `longestGap`,
`commitStyle`, `releases`, `signatureWord`.

### warnings

Plain sentences about things that make the numbers less complete than they look: a shallow clone,
git output that could not be parsed or had to be truncated, per-commit file lists that were cut,
hotspot tracking that stopped at its limit, or `No commits matched the given filters.`
`gitstory` prints them on stderr, and the report shows them in a notice.

## Limits

Everything that comes out of a repository is bounded, so a hostile or enormous repository cannot
exhaust memory or time:

| What | Limit |
|---|---|
| Authors in the leaderboard | 7, plus one folded row |
| Hotspot files / languages / words | 15 / 6 (plus `Other`) / 30 |
| Weekly / monthly series | 1,040 / 480 points |
| Files itemised per commit | 50,000 (line totals still include the rest) |
| Distinct files tracked for hotspots | 500,000 |
| Tags read / listed | 2,000 / 50 |
| Author name, subject line, file path | 200 / 500 / 1,024 characters |
| One token of git output | 1 MiB |

## Things a number cannot tell you

- **Commits are not work.** A squash-merge workflow shows one big commit where there were twenty;
  a bot that commits every night looks like your most dedicated author (dependabot is in the
  example data on purpose).
- **One person, several identities.** Without a `.mailmap`, the same person using two e-mail
  addresses counts as two authors.
- **Time zones are the author's.** A traveller's commits are spread over several zones; the
  chronotype describes the clock they saw.
- **Shallow clones lie by omission.** The report says so (`repository.shallow`), but the numbers
  only cover what was fetched.
- **The bus factor and the chronotype are heuristics**, labelled as such in the data.
- **Line counts depend on what git sees.** Moved code can look like deleted-and-added code, and
  `-M` rename detection is git's, not gitstory's.
