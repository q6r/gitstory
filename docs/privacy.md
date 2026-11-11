# Privacy: what leaves your repository

**Nothing.** gitstory reads a repository on your machine and writes files on your machine. It has
no telemetry, no update check, no analytics and no network client of any kind: the report and the
card work offline, and the code contains no URL it ever requests.

This page lists what is read, what is written and what you should think about before you share
an output.

## What is read

gitstory starts exactly one other program, `git`, and asks it for:

- **Commit metadata**: hash, parent hashes, author name, author e-mail (after `.mailmap`),
  author date and the **subject line** (the first line of the message).
- **Per-file change counts**: for every commit, the path of each changed file and its added and
  deleted line **counts** (`git log --numstat`). Not the lines themselves.
- **Tags**: name, date and kind of every tag.
- **Repository facts**: the root, the current branch and commit, whether it is a shallow clone.

It never reads file contents, commit message bodies, diffs, the working tree, your git
configuration (beyond what git itself applies) or any other repository. It does not run hooks,
external diff tools or text converters (`--no-ext-diff --no-textconv`), and nothing from the
repository is ever executed, evaluated or used as a command.

## What is written

| Output | Where | What is in it |
|---|---|---|
| `gitstory summary` | your terminal | Counts and charts; author names, file paths, the subject of the biggest commit and tag names. |
| `gitstory json` | stdout or the file you name | The [metrics document](metrics.md). |
| `gitstory report` | the file you name | The same document, inside one HTML file. |
| `gitstory card` | the file you name | The repository name (or your `--title`), counts, a heatmap and language names. |
| `gitstory serve` | memory, answered on `127.0.0.1` | The same as `report`, `json` and `card`, to whoever can reach the port. |

gitstory creates no other files: no cache, no database, no configuration. (The one scratch file is
an anonymous temporary file that holds the error text of the running `git` process; the operating
system deletes it when the process ends.)

## What an output reveals

The numbers are aggregates, but the following come **from your repository as text**, so look at
them before you publish a report of a private project:

- **Author names** (never e-mail addresses: those are used to tell people apart and are dropped).
- **File paths** of the 15 hottest files, and the names of the file types.
- **The subject line of the biggest commit**, and the **most common words** in all subjects.
- **Tag names and dates.**
- **The repository name** (the folder name) and the current **branch and commit id**.
- **When people work**: the weekday x hour grid and the chronotype describe each repository's
  rhythm, not a person, but with one author that *is* a person. Think twice before publishing a
  per-person report (`--author`) of somebody else.

The card is the smallest of them: repository name, three counts, a heatmap of commit counts per
day and the names of the main languages. `--title` lets you replace even the name.

Warnings printed on stderr and the `warnings` list in the JSON are about the history (a shallow
clone, unparseable output), not about you.

## `serve` and the network

`gitstory serve` listens on `127.0.0.1` by default and **refuses any other address** unless you
pass `--allow-remote`, because it has no authentication: anyone who can connect can read the
data of the repository. It also refuses requests whose `Host` header is not its own address
(the defence against DNS rebinding, where a web page tries to reach your local server), only lets
same-origin pages call the one endpoint that does something (`POST /api/refresh`), never reads a
file named by a URL, and sets `X-Frame-Options`, `Cross-Origin-Resource-Policy` and a strict
content-security policy on every response. See the [security notes](../README.md#security-notes).

If you put the server behind a reverse proxy or a tunnel, you are giving the data to whoever can
reach that address.

## The page itself

The HTML report loads nothing from the network: no fonts, no scripts, no images, no analytics.
Its content-security policy (`default-src 'none'`, `connect-src 'self'`, images only from
`data:`) enforces that in the browser, so even a bug could not make it phone home. The theme you
choose with the toggle is remembered in your browser's `localStorage` under the key
`aurora-theme`; nothing else is stored.

## Reproducibility

Because the output is a pure function of the history and the clock, you can regenerate any file
and compare it; with `SOURCE_DATE_EPOCH` set, even the timestamp inside is the same.
