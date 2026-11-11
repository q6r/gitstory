"""Deterministic synthetic git repositories for the tests and the examples.

``build_synthetic_repo(path)`` writes roughly 700 commits spread over almost six years,
by a dozen authors in different timezones, with feature branches and merge commits,
renames (pure and edited), binary files, unicode file names, a ``.mailmap`` and tags
(annotated and lightweight). It is generated with ``git fast-import`` from a seeded
``random.Random``: the author/committer dates are fixed per commit (the same thing
``GIT_AUTHOR_DATE``/``GIT_COMMITTER_DATE`` would do), so every run, on every machine,
produces the same commit ids. Building takes a fraction of a second.

Alongside the repository the builder returns an *oracle*: an independent, hand-kept record
of every commit (author, local time, file changes with line counts, intended
conventional-commit type, parents) that the tests compare the collector and the metrics
against. A few facts are planted on purpose so they can be asserted exactly: a 71-day quiet
gap, a 21-day streak, a 17-commit "hack day", a 4,000-line import, renames, and a streak
that is still alive on the fixed "now".

``make_real_repo`` builds tiny repositories with plain ``git commit`` for the states that
fast-import cannot express (detached HEAD, shallow clones, real merges).
"""

from __future__ import annotations

import os
import random
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
START = date(2021, 1, 4)
END = date(2026, 11, 9)

GAP_FIRST, GAP_LAST = date(2023, 6, 3), date(2023, 8, 12)  # 71 quiet days
STREAK_FIRST, STREAK_LAST = date(2024, 2, 5), date(2024, 2, 25)  # 21 active days
HACK_DAY = date(2024, 9, 12)  # exactly HACK_DAY_COMMITS commits
HACK_DAY_COMMITS = 17
IMPORT_DAY = date(2022, 3, 15)  # the biggest commit: IMPORT_LINES added lines
IMPORT_LINES = 4000
FINAL_STREAK = (date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9))
QUIET_EXTRA = (date(2024, 2, 4), date(2024, 2, 26), date(2026, 11, 5))


def git_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that isolates git from the machine's own configuration."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Fixture",
        GIT_AUTHOR_EMAIL="fixture@example.com",
        GIT_COMMITTER_NAME="Fixture",
        GIT_COMMITTER_EMAIL="fixture@example.com",
    )
    if extra:
        env.update(extra)
    return env


def run_git(
    repo: Path, *args: str, stdin: bytes | None = None, env: dict[str, str] | None = None
) -> str:
    """Run git in ``repo`` with an isolated configuration and return stdout (raises on error)."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        env=git_environment(env),
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace')}")
    return done.stdout.decode("utf-8", errors="replace")


# ------------------------------------------------------------------------------------ people


@dataclass(frozen=True)
class Person:
    """An author of the synthetic history."""

    name: str
    email: str
    offset_minutes: int
    hours: tuple[int, ...]
    weight: float = 0.0
    portuguese: bool = False
    prefix_rate: float = 0.0
    old_name: str | None = None
    old_email: str | None = None

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(minutes=self.offset_minutes))


ADA = Person("Ada Lovelace", "ada@example.com", -180, (19, 20, 21, 22, 23, 0), 6, prefix_rate=0.8)
GRACE = Person("Grace Hopper", "grace@example.com", 0, (9, 10, 11, 14, 15, 16), 4, prefix_rate=0.7)
ZOE = Person("Zoë Müller", "zoe@example.org", 60, (6, 7, 8, 9), 3, prefix_rate=0.5)
LI = Person("李雷", "lilei@example.cn", 480, (10, 11, 15, 16, 17), 2, prefix_rate=0.3)
JOAO = Person(
    "João Silva",
    "joao@example.com.br",
    -180,
    (9, 10, 14, 15, 17),
    3,
    portuguese=True,
    old_name="joao silva",
    old_email="joao.silva@old.example.com",
)
ALAN = Person("Alan Turing", "alan@example.net", 0, (13, 14, 17, 18), 2, prefix_rate=0.1)
BOT = Person("dependabot[bot]", "bot@example.org", 0, (3,), prefix_rate=1.0)
PRIMARY = (ADA, GRACE, ZOE, LI, JOAO, ALAN)
MAINTAINERS = (ADA, GRACE, ALAN)
CONTRIBUTOR_OFFSETS = (-480, -300, 0, 330, 540, 120, -60, 600)
CONTRIBUTORS = tuple(
    Person(f"Casey Contributor {i}", f"casey{i}@example.net", offset, (10, 11, 14))
    for i, offset in enumerate(CONTRIBUTOR_OFFSETS, start=1)
)

TAGS = (
    ("v0.1.0", date(2021, 3, 1), True),
    ("v0.2.0", date(2021, 6, 14), False),
    ("v0.3.0", date(2021, 10, 4), True),
    ("v1.0.0", date(2022, 2, 7), True),
    ("v1.1.0", date(2022, 7, 18), False),
    ("v1.2.0", date(2022, 12, 5), True),
    ("v1.2.1", date(2023, 2, 13), False),
    ("v1.3.0", date(2023, 9, 25), True),
    ("v2.0.0-rc.1", date(2024, 4, 15), False),
    ("v2.0.0", date(2024, 6, 3), True),
    ("v2.1.0", date(2024, 11, 18), True),
    ("v2.2.0", date(2025, 5, 12), False),
    ("v2.3.0", date(2026, 3, 9), True),
    ("v2.3.1", date(2026, 9, 21), True),
)

CONVENTIONAL_VERBS = {
    "feat": ("add {t}", "support {t}", "introduce {t}"),
    "fix": ("handle {t} edge case", "correct {t} calculation", "stop {t} crashing"),
    "docs": ("describe {t}", "update the {t} guide"),
    "chore": ("tidy {t}", "clean up {t}"),
    "refactor": ("simplify {t}", "extract {t} helper"),
    "test": ("cover {t}",),
    "perf": ("speed up {t}",),
    "ci": ("cache {t}",),
    "build": ("pin {t}",),
    "style": ("format {t}",),
}
CONVENTIONAL_WEIGHTS = (30, 28, 10, 8, 8, 7, 3, 2, 2, 2)
THINGS = (
    "parser", "login flow", "cache layer", "router", "config loader", "dark mode", "exporter",
    "scheduler", "search index", "retry logic", "metrics endpoint", "settings page",
)  # fmt: skip
PLAIN_MESSAGES = (
    "Update README", "Fix typo in docs", "WIP", "Refactor router", "Add tests for cache",
    "Tweak styles", "Bump version", "Remove dead code", "Improve error messages",
)  # fmt: skip
PT_VERBS = (
    "adiciona {c}", "corrige {c}", "atualiza a documentação de {c}",
    "remove código morto em {c}", "melhora o desempenho de {c}", "ajusta os testes de {c}",
)  # fmt: skip
PT_THINGS = ("tela de login", "cache", "rotas", "relatório mensal", "exportação", "busca")
POOLS = {
    ADA.name: ("src/app/", "tests/"),
    GRACE.name: ("docs/", "src/app/", "README.md"),
    ZOE.name: ("src/web/",),
    LI.name: ("crates/", "src/app/"),
    JOAO.name: ("src/app/", "docs/"),
    ALAN.name: (".github/", "scripts/", "src/app/"),
}
HOT_FILES = ("src/app/core.py", "src/web/index.ts", "src/web/main.ts")

# date -> (author, event): one planted commit on that day
PLANTED = {
    IMPORT_DAY: (GRACE, "seed"),
    date(2022, 5, 10): (ADA, "rename-utils"),
    date(2022, 9, 5): (LI, "rust-crate"),
    date(2023, 9, 1): (ADA, "logo"),
    date(2024, 3, 4): (ZOE, "rename-index"),
    date(2025, 1, 20): (GRACE, "move-guide"),
}
PLANTED_SUBJECTS = {
    "seed": ("chore: vendor the seed dataset", "chore"),
    "rename-utils": ("refactor: rename utils to helpers", "refactor"),
    "rust-crate": ("feat(fast): add the Rust fast path", "feat"),
    "logo": ("chore: refresh the logo", "chore"),
    "rename-index": ("refactor(web): rename index to main", "refactor"),
    "move-guide": ("docs: move the guide into the handbook", "docs"),
}


# ----------------------------------------------------------------------------------- oracle


@dataclass
class SpecChange:
    """One file change of an oracle commit (``fid`` follows a file through renames)."""

    fid: int
    path: str
    additions: int
    deletions: int
    binary: bool = False
    old_path: str | None = None


@dataclass
class SpecCommit:
    """Ground truth for one commit."""

    index: int
    when: datetime
    author: str
    email: str
    raw_name: str
    raw_email: str
    subject: str
    kind: str | None
    parents: tuple[int, ...]
    changes: list[SpecChange] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def additions(self) -> int:
        return sum(c.additions for c in self.changes)

    @property
    def deletions(self) -> int:
        return sum(c.deletions for c in self.changes)

    @property
    def local_day(self) -> date:
        return self.when.date()


@dataclass
class SpecTag:
    """Ground truth for one tag."""

    name: str
    created_at: datetime
    annotated: bool
    commit_index: int


@dataclass
class SyntheticRepo:
    """A built repository plus its oracle."""

    path: Path
    commits: list[SpecCommit]
    tags: list[SpecTag]
    now: datetime = NOW

    @property
    def non_merges(self) -> list[SpecCommit]:
        return [c for c in self.commits if not c.is_merge]


# --------------------------------------------------------------------------------- builder


@dataclass
class _File:
    fid: int
    path: str
    lines: list[str] = field(default_factory=list)
    blob: bytes | None = None

    def content(self) -> bytes:
        if self.blob is not None:
            return self.blob
        return ("\n".join(self.lines) + "\n").encode()


@dataclass
class _Feature:
    slug: str
    who: Person
    start: date
    end: date
    files: dict[str, _File] = field(default_factory=dict)
    tip: int | None = None

    @property
    def ref(self) -> str:
        return f"refs/heads/feature/{self.slug}"


def quiet(day: date) -> bool:
    """Days on which the synthetic history has no commits at all."""
    return GAP_FIRST <= day <= GAP_LAST or day in QUIET_EXTRA


class _Builder:
    """Generates the fast-import stream and the oracle in one chronological pass."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.stream = bytearray()
        self.commits: list[SpecCommit] = []
        self.tags: list[SpecTag] = []
        self.marks: list[int] = []  # commit index -> fast-import mark
        self.files: dict[str, _File] = {}
        self.counter = 0
        self.next_fid = 1
        self.main_tip: int | None = None
        self.features: list[_Feature] = []
        self.pending_tags = list(TAGS)
        self.bot_days = {START + timedelta(days=d) for d in range(20, (END - START).days, 38)}
        self.contributor_days = self._plan_contributors()

    # ---- helpers -----------------------------------------------------------------------

    @staticmethod
    def _data(payload: bytes) -> bytes:
        return b"data %d\n" % len(payload) + payload + b"\n"

    @staticmethod
    def _ident(name: str, email: str, when: datetime) -> bytes:
        offset = when.utcoffset()
        assert offset is not None
        minutes = int(offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        stamp = f"{int(when.timestamp())} {sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"
        return f"{name} <{email}> {stamp}".encode()

    def _fresh_line(self, path: str) -> str:
        self.counter += 1
        return f"line {self.counter} of {path}"

    def _new_file(self, path: str, lines: int) -> _File:
        created = _File(self.next_fid, path, [self._fresh_line(path) for _ in range(lines)])
        self.next_fid += 1
        self.files[path] = created
        return created

    def _plan_contributors(self) -> dict[date, Person]:
        planned: dict[date, Person] = {}
        for i, person in enumerate(CONTRIBUTORS):
            day = START + timedelta(days=60 + 97 * i + self.rng.randint(0, 20))
            if day <= END and not quiet(day) and day != HACK_DAY and day not in PLANTED:
                planned[day] = person
        return planned

    def _when(self, day: date, who: Person) -> datetime:
        hour = self.rng.choice(who.hours)
        minute, second = self.rng.randint(0, 59), self.rng.randint(0, 59)
        return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=who.tz)

    def _message(self, who: Person, scope: str | None = None) -> tuple[str, str | None]:
        rng = self.rng
        if who is BOT:
            name = rng.choice(("lodash", "react", "vite"))
            return f"build(deps): bump {name} to {rng.randint(2, 9)}.{rng.randint(0, 9)}", "build"
        if who.portuguese:
            return rng.choice(PT_VERBS).format(c=rng.choice(PT_THINGS)), None
        if rng.random() < who.prefix_rate:
            kind = rng.choices(list(CONVENTIONAL_VERBS), CONVENTIONAL_WEIGHTS)[0]
            bang = "!" if rng.random() < 0.03 else ""
            verb = rng.choice(CONVENTIONAL_VERBS[kind]).format(t=rng.choice(THINGS))
            area = scope or rng.choice(("core", "api", "ui", "cli"))
            return f"{kind}({area}){bang}: {verb}", kind
        return rng.choice(PLAIN_MESSAGES), None

    # ---- file operations -----------------------------------------------------------------

    def _modify(self, target: _File) -> SpecChange:
        replace = min(len(target.lines), self.rng.randint(1, 6))
        append = self.rng.randint(0, 8) if self.rng.random() < 0.6 else 0
        for i in self.rng.sample(range(len(target.lines)), replace):
            target.lines[i] = self._fresh_line(target.path)
        target.lines.extend(self._fresh_line(target.path) for _ in range(append))
        return SpecChange(target.fid, target.path, replace + append, replace)

    def _candidates(self, who: Person) -> list[_File]:
        text_files = sorted(
            (f for f in self.files.values() if f.blob is None), key=lambda f: f.path
        )
        prefixes = POOLS.get(who.name, ("src/app/",))
        chosen = [f for f in text_files if f.path.startswith(prefixes)]
        return chosen or text_files

    def _ordinary_changes(self, who: Person) -> list[SpecChange]:
        rng = self.rng
        if who is BOT:
            return [self._modify(self.files[p]) for p in ("package.json", "package-lock.json")]
        roll = rng.random()
        doomed = sorted(p for p in self.files if "/mod_" in p)
        if roll < 0.03 and doomed:  # delete a module created earlier by the generator
            gone = self.files.pop(rng.choice(doomed))
            return [SpecChange(gone.fid, gone.path, 0, len(gone.lines))]
        candidates = self._candidates(who)
        weights = [3.0 if f.path in HOT_FILES else 1.0 for f in candidates]
        count = rng.choices((1, 2, 3, 4), (45, 30, 18, 7))[0]
        picked = {f.fid: f for f in rng.choices(candidates, weights, k=count)}
        changes = [self._modify(f) for f in picked.values()]
        if roll > 0.88:  # a brand new file
            ext, folder = rng.choice((("py", "src/app"), ("ts", "src/web"), ("md", "docs")))
            fresh = self._new_file(f"{folder}/mod_{self.next_fid}.{ext}", rng.randint(5, 60))
            changes.append(SpecChange(fresh.fid, fresh.path, len(fresh.lines), 0))
        return changes

    def _planted_changes(self, event: str) -> list[SpecChange]:
        if event == "seed":
            seed = self._new_file("data/seed.json", IMPORT_LINES)
            return [SpecChange(seed.fid, seed.path, IMPORT_LINES, 0)]
        if event == "rust-crate":
            crate = self._new_file("crates/fast/src/lib.rs", 80)
            return [SpecChange(crate.fid, crate.path, 80, 0)]
        if event == "logo":
            logo = self.files["assets/logo.png"]
            logo.blob = b"\x89PNG\r\n\x1a\n\x00" + self.rng.randbytes(500)
            return [SpecChange(logo.fid, logo.path, 0, 0, binary=True)]
        moves = {
            "rename-utils": ("src/app/utils.py", "src/app/helpers.py", False),
            "rename-index": ("src/web/index.ts", "src/web/main.ts", True),
            "move-guide": ("docs/guide.md", "docs/handbook/guide.md", False),
        }
        old, new, edit = moves[event]
        moved = self.files.pop(old)
        moved.path = new
        self.files[new] = moved
        if edit:  # a rename with one changed line: similarity stays high, numstat is 1/1
            moved.lines[3] = self._fresh_line(new)
        return [SpecChange(moved.fid, new, int(edit), int(edit), old_path=old)]

    # ---- emitting commits ------------------------------------------------------------------

    def _ops(self, changes: list[SpecChange], present: dict[str, _File]) -> list[bytes]:
        """Translate oracle changes into fast-import file commands (D + M, never R)."""
        ops: list[bytes] = []
        for change in changes:
            if change.old_path is not None:
                ops.append(f"D {change.old_path}\n".encode())
            if change.path in present:
                body = self._data(present[change.path].content())
                ops.append(f"M 100644 inline {change.path}\n".encode() + body)
            else:
                ops.append(f"D {change.path}\n".encode())
        return ops

    def _emit(
        self,
        who: Person,
        when: datetime,
        subject: str,
        kind: str | None,
        changes: list[SpecChange],
        ops: list[bytes],
        *,
        ref: str,
        parents: tuple[int, ...],
        branch_from: int | None = None,
        merge: int | None = None,
        old_identity: bool = False,
    ) -> int:
        raw_name = who.old_name if old_identity and who.old_name else who.name
        raw_email = who.old_email if old_identity and who.old_email else who.email
        body = ""
        if self.rng.random() < 0.2:
            body = f"\n\nRefs #{self.counter}\nThe body is never read by gitstory."
        mark = len(self.marks) + 1
        out = bytearray(f"commit {ref}\nmark :{mark}\n".encode())
        out += b"author " + self._ident(raw_name, raw_email, when) + b"\n"
        out += b"committer " + self._ident(who.name, who.email, when) + b"\n"
        out += self._data((subject + body).encode())
        if branch_from is not None:
            out += f"from :{self.marks[branch_from]}\n".encode()
        if merge is not None:
            out += f"merge :{self.marks[merge]}\n".encode()
        out += b"".join(ops) + b"\n"
        self.stream += out
        index = len(self.commits)
        self.marks.append(mark)
        self.commits.append(
            SpecCommit(
                index,
                when,
                who.name,
                who.email,
                raw_name,
                raw_email,
                subject,
                kind,
                parents,
                changes,
            )
        )
        return index

    def _main_commit(
        self,
        who: Person,
        day: date,
        *,
        subject: tuple[str, str | None] | None = None,
        changes: list[SpecChange] | None = None,
        when: datetime | None = None,
    ) -> None:
        use = changes if changes is not None else self._ordinary_changes(who)
        text, kind = subject or self._message(who)
        old_identity = (
            who.old_name is not None and day < date(2022, 7, 1) and self.rng.random() < 0.6
        )
        parents = (self.main_tip,) if self.main_tip is not None else ()
        self.main_tip = self._emit(
            who, when or self._when(day, who), text, kind, use, self._ops(use, self.files),
            ref="refs/heads/main", parents=parents, old_identity=old_identity,
        )  # fmt: skip

    def _initial_commit(self) -> None:
        sizes = {
            "README.md": 30, "LICENSE": 20, "src/app/core.py": 60, "src/app/utils.py": 40,
            "src/app/models.py": 30, "src/web/index.ts": 50, "src/web/styles.css": 40,
            "tests/test_core.py": 30, "package.json": 20, "package-lock.json": 300,
            "docs/guide.md": 25, ".github/workflows/ci.yml": 25, "scripts/deploy.sh": 15,
            "docs/café.md": 10, "docs/日本語.md": 8,
        }  # fmt: skip
        changes = []
        for path, count in sizes.items():
            created = self._new_file(path, count)
            changes.append(SpecChange(created.fid, path, count, 0))
        mailmap = _File(self.next_fid, ".mailmap")
        mailmap.lines = [f"{JOAO.name} <{JOAO.email}> {JOAO.old_name} <{JOAO.old_email}>"]
        self.next_fid += 1
        self.files[mailmap.path] = mailmap
        changes.append(SpecChange(mailmap.fid, mailmap.path, 1, 0))
        logo = _File(
            self.next_fid,
            "assets/logo.png",
            blob=b"\x89PNG\r\n\x1a\n\x00" + self.rng.randbytes(400),
        )
        self.next_fid += 1
        self.files[logo.path] = logo
        changes.append(SpecChange(logo.fid, logo.path, 0, 0, binary=True))
        when = datetime(2021, 1, 4, 21, 5, 9, tzinfo=ADA.tz)
        self._main_commit(
            ADA, START, subject=("chore: initial import", "chore"), changes=changes, when=when
        )

    # ---- feature branches ----------------------------------------------------------------

    def _feature_step(self, day: date) -> None:
        rng = self.rng
        if quiet(day) or day == HACK_DAY:
            return
        starts = day.weekday() < 5 and rng.random() < 0.035 and len(self.features) < 2
        before_gap = GAP_FIRST - timedelta(days=8) <= day <= GAP_FIRST
        if starts and day < END - timedelta(days=12) and not before_gap:
            topic = rng.choice(("search", "export", "theme", "auth", "cache", "report"))
            slug = f"{topic}-{len(self.commits)}"
            end = day + timedelta(days=rng.randint(2, 6))
            self.features.append(_Feature(slug, rng.choice(PRIMARY), day, end))
        for feature in list(self.features):
            if feature.start != day and day <= feature.end and day.weekday() < 5:
                self._feature_commit(feature, day)
            if day >= feature.end:
                self._merge(feature, day)
                self.features.remove(feature)

    def _feature_commit(self, feature: _Feature, day: date) -> None:
        path = f"features/{feature.slug}/part{len(feature.files) % 3}.py"
        if path in feature.files:
            target = feature.files[path]
            change = self._modify(target)
        else:
            target = _File(
                self.next_fid,
                path,
                [self._fresh_line(path) for _ in range(self.rng.randint(10, 40))],
            )
            self.next_fid += 1
            feature.files[path] = target
            change = SpecChange(target.fid, path, len(target.lines), 0)
        subject, kind = self._message(feature.who, scope=feature.slug)
        first = feature.tip is None
        parent = self.main_tip if first else feature.tip
        assert parent is not None
        feature.tip = self._emit(
            feature.who, self._when(day, feature.who), subject, kind, [change],
            self._ops([change], {path: target}),
            ref=feature.ref, parents=(parent,), branch_from=parent if first else None,
        )  # fmt: skip

    def _merge(self, feature: _Feature, day: date) -> None:
        if feature.tip is None or self.main_tip is None:
            return
        merger = self.rng.choice(MAINTAINERS)
        ops = []
        for path, target in sorted(feature.files.items()):
            self.files[path] = target
            ops.append(f"M 100644 inline {path}\n".encode() + self._data(target.content()))
        when = self._when(day, merger) + timedelta(minutes=7)
        self.main_tip = self._emit(
            merger, when, f"Merge branch 'feature/{feature.slug}'", None, [], ops,
            ref="refs/heads/main", parents=(self.main_tip, feature.tip), merge=feature.tip,
        )  # fmt: skip

    # ---- tags ---------------------------------------------------------------------------

    def _emit_tags(self, day: date) -> None:
        while self.pending_tags and self.pending_tags[0][1] <= day and self.main_tip is not None:
            name, _day, annotated = self.pending_tags.pop(0)
            target = self.commits[self.main_tip]
            mark = self.marks[self.main_tip]
            if annotated:
                created = datetime(day.year, day.month, day.day, 15, 30, tzinfo=timezone.utc)
                tagger = f"{ADA.name} <{ADA.email}> {int(created.timestamp())} +0000"
                header = f"tag {name}\nfrom :{mark}\ntagger {tagger}\n"
                self.stream += header.encode() + self._data(f"Release {name}".encode())
            else:
                created = target.when
                self.stream += f"reset refs/tags/{name}\nfrom :{mark}\n\n".encode()
            self.tags.append(SpecTag(name, created, annotated, target.index))

    # ---- driver -------------------------------------------------------------------------

    def _count_for(self, who: Person, day: date) -> int:
        rate = {2021: 0.8, 2022: 1.15, 2023: 0.8, 2024: 1.3, 2025: 1.0, 2026: 0.85}[day.year]
        if day.month == 12:
            rate *= 0.45
        if day.weekday() >= 5:
            rate *= 0.28
        probability = min(0.9, rate * who.weight / 55.0)
        count = 0
        while count < 4 and self.rng.random() < probability:
            count += 1
            probability *= 0.45
        return count

    def _must_commit(self, day: date) -> bool:
        edges = (GAP_FIRST - timedelta(days=1), GAP_LAST + timedelta(days=1))
        return STREAK_FIRST <= day <= STREAK_LAST or day in FINAL_STREAK or day in edges

    def _one_day(self, day: date) -> None:
        if day in PLANTED:
            who, event = PLANTED[day]
            self._main_commit(
                who, day, subject=PLANTED_SUBJECTS[event], changes=self._planted_changes(event)
            )
        if day == HACK_DAY:
            for _ in range(HACK_DAY_COMMITS):
                self._main_commit(self.rng.choice(PRIMARY), day)
            return
        authors = [who for who in PRIMARY for _ in range(self._count_for(who, day))]
        if day in self.bot_days:
            authors.append(BOT)
        if not authors and self._must_commit(day) and day not in PLANTED:
            authors.append(self.rng.choice(PRIMARY))
        for who in authors:
            self._main_commit(who, day)
        if day in self.contributor_days:
            self._main_commit(self.contributor_days[day], day, subject=("Fix typo in docs", None))

    def build(self) -> None:
        self._initial_commit()
        day = START
        while day <= END:
            if day != START and not quiet(day):
                self._one_day(day)
            self._feature_step(day)
            self._emit_tags(day)
            day += timedelta(days=1)


def build_synthetic_repo(path: Path, *, seed: int = 20260110) -> SyntheticRepo:
    """Build the synthetic repository in ``path`` (created if needed) and return its oracle."""
    builder = _Builder(seed)
    builder.build()
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "fast-import", "--quiet", stdin=bytes(builder.stream))
    run_git(path, "checkout", "-q", "-f", "main")
    return SyntheticRepo(path=path, commits=builder.commits, tags=builder.tags)


# ----------------------------------------------------------------------------- hostile repo

HOSTILE_SUBJECTS = (
    "$(touch pwned) `touch pwned2` ; touch pwned3 %s %H ${IFS} {{data}} {{title}}",
    "<img src=x onerror=alert(1)> </script><script>alert(2)</script> <!--",
    "x" * 6000,
    "\x01feedfacecafe fake marker and \x1b[2J clear screen \x1b]0;pwned\x07",
    "feat: " + "\N{RIGHT-TO-LEFT OVERRIDE}gnp.exe",
)
HOSTILE_AUTHORS = (
    ("Mallory \x1b[31mRED\x1b[0m \x1b]0;pwned\x07", "m@evil.example"),
    ("Eve \N{RIGHT-TO-LEFT OVERRIDE}gnp.exe", "eve@evil.example"),
    ("&lt;script&gt;alert(1)&lt;/script&gt;", "amp@evil.example"),
    ('" onmouseover="alert(1)" x=\'', "quote@evil.example"),
    ("{{data}}", "tpl@evil.example"),
)
# (fast-import quoted form, the real path)
HOSTILE_PATHS = (
    ('"quote\\"d.txt"', 'quote"d.txt'),
    ('"tab\\there.txt"', "tab\there.txt"),
    ('"new\\nline.txt"', "new\nline.txt"),
    ("-dash.txt", "-dash.txt"),
    ("=> arrow.txt", "=> arrow.txt"),
    ("{x => y}.txt", "{x => y}.txt"),
    (
        "sp ace/caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt",
        "sp ace/caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt",
    ),
)


def build_hostile_repo(path: Path) -> list[str]:
    """Build a bare repository full of hostile content and return its real file paths.

    It is bare because many of these paths cannot exist in a Windows working tree; the
    paths are written straight into the object database with ``core.protectNTFS=false``.
    """
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "--bare", "-b", "main")
    stream = bytearray()
    when = 1_700_000_000
    for index in range(max(len(HOSTILE_SUBJECTS), len(HOSTILE_AUTHORS))):
        name, email = HOSTILE_AUTHORS[index % len(HOSTILE_AUTHORS)]
        subject = HOSTILE_SUBJECTS[index % len(HOSTILE_SUBJECTS)]
        stamp = when + index * 3600
        ident = f"{name} <{email}> {stamp} +0000".encode()
        stream += b"commit refs/heads/main\nauthor " + ident + b"\ncommitter " + ident + b"\n"
        message = subject.encode()
        stream += b"data %d\n" % len(message) + message + b"\n"
        for quoted, _real in HOSTILE_PATHS[index::2]:
            content = f"{index} {quoted}\n".encode()
            stream += f"M 100644 inline {quoted}\n".encode()
            stream += b"data %d\n" % len(content) + content + b"\n"
        stream += b"\n"
    run_git(path, "-c", "core.protectNTFS=false", "fast-import", "--quiet", stdin=bytes(stream))
    return [real for _quoted, real in HOSTILE_PATHS]


# ----------------------------------------------------------------------------- real git repos


def make_real_repo(path: Path, dates: list[str], *, branch: str = "main") -> Path:
    """Create a repo with plain ``git commit`` calls, one per ISO date in ``dates``."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", branch)
    for i, stamp in enumerate(dates):
        (path / "file.txt").write_text(f"revision {i}\n", encoding="utf-8")
        run_git(path, "add", "-A")
        env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        run_git(path, "commit", "-q", "-m", f"commit {i}", env=env)
    return path
