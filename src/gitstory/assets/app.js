/* gitstory report: renders the metrics (gitstory.metrics/v1) into the Aurora layout.
 *
 * The data arrives as an inert JSON block; every string is placed with textContent (through
 * Aurora.el), never as HTML, so nothing from the repository can become markup or script. */
(function () {
  'use strict';

  const A = window.Aurora;
  const C = window.AuroraCharts;
  const el = A.el;
  const fmt = A.fmt;

  // `--theme dark|light` is written into the page and beats the visitor's stored choice;
  // with `auto` the choice made with the toggle wins.
  const pinnedTheme = document.documentElement.getAttribute('data-theme');
  A.init({ forceTheme: pinnedTheme === 'dark' || pinnedTheme === 'light' });

  const data = JSON.parse(document.getElementById('report-data').textContent);
  const live = document.body.getAttribute('data-live') === 'true';
  const repo = data.repository;
  const hasCommits = data.totals.commits > 0;

  // The sections of the report, in page order: the rail, the breadcrumbs, the command palette and
  // the scroll spy all read this table. An empty history only has the first one.
  const SECTIONS = [
    ['story', 'Story', 'book'],
    ['activity', 'Activity', 'activity'],
    ['people', 'People', 'users'],
    ['code', 'Code', 'code'],
    ['messages', 'Messages', 'message'],
    ['releases', 'Releases', 'tag'],
    ['card', 'README card', 'image'],
  ];
  const sections = hasCommits ? SECTIONS : SECTIONS.slice(0, 1);

  /* ── small helpers ─────────────────────────────────────────────────────────────────────── */

  const int = (n) => fmt.int(n);
  const pct = (ratio, digits) => fmt.pct(ratio, digits == null ? 0 : digits);
  const plural = (n, one, many) => `${int(n)} ${n === 1 ? one : many || `${one}s`}`;
  const day = (iso) => String(iso || '').slice(0, 10);
  const localDate = (iso) => new Date(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const dateText = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  const monthText = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short' });
  const NBSP = String.fromCharCode(160);
  // a date is not broken across lines
  const longDate = (iso) => dateText.format(localDate(day(iso))).split(' ').join(NBSP);
  const hourText = (h) => `${String(h).padStart(2, '0')}:00`;
  const lastMinute = (to) => `${String((to + 23) % 24).padStart(2, '0')}:59`;
  const add = (list) => list.reduce((total, n) => total + n, 0);

  function spanText(days) {
    const years = Math.floor(days / 365);
    const months = Math.floor((days % 365) / 30);
    if (years && months) return `${plural(years, 'year')} ${plural(months, 'month')}`;
    if (years) return plural(years, 'year');
    if (months) return plural(months, 'month');
    return plural(days, 'day');
  }

  function shortSpan(days) {
    const years = Math.floor(days / 365);
    const months = Math.floor((days % 365) / 30);
    return [years ? `${years}y` : '', months ? `${months}m` : ''].filter(Boolean).join(' ') || `${days}d`;
  }

  // A hint that keeps each date in one piece: the line may break between the parts, not inside one.
  const nowrap = (text) => el('span', { class: 'gs-nowrap' }, text);
  function richHint(stat, parts) {
    const hint = stat.el.querySelector('.a-stat__hint');
    if (!hint) return;
    while (hint.firstChild) hint.removeChild(hint.firstChild);
    parts.forEach((part) => hint.appendChild(typeof part === 'string' ? document.createTextNode(part) : part));
  }
  function statTile(parent, options) {
    const stat = A.stat(parent, options);
    if (options.rich) richHint(stat, options.rich);
    return stat;
  }

  function cell(parent, cls) {
    const node = el('div', { class: cls });
    parent.appendChild(node);
    return node;
  }

  function onVisible(node, fn) {
    if (!('IntersectionObserver' in window)) { fn(); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { io.disconnect(); fn(); }
    }, { threshold: 0.3 });
    io.observe(node);
  }

  function filterChips() {
    const f = data.filters;
    const chips = [];
    if (f.since) chips.push(`since ${day(f.since)}`);
    if (f.until) chips.push(`until ${day(f.until)}`);
    if (f.author) chips.push(`author: ${f.author}`);
    if (f.branch) chips.push(`rev: ${f.branch}`);
    f.paths.forEach((p) => chips.push(`path: ${p}`));
    if (f.noMerges) chips.push('no merge commits');
    if (f.maxCommits) chips.push(`latest ${int(f.maxCommits)} commits`);
    return chips;
  }

  /* ── the data export, the live page and the Charts/Tables switch ─────────────────────── */

  function slug(text) {
    return String(text).replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'report';
  }

  function downloadJson() {
    const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = el('a', { download: `${slug(repo.name)}-gitstory.json` });
    link.href = url;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  // A page served by `gitstory serve` shows a live indicator and a refresh button in the top bar.
  function setupLive() {
    if (!live) return { nodes: [], refresh: null };
    const status = A.live(null, { label: 'Live' });
    status.set('live', 'Live');
    const label = 'Refresh the data from the repository';
    const button = el('button', { class: 'a-nav-btn', type: 'button', 'aria-label': label, title: label }, A.icon('refresh', { size: 17 }));
    async function refresh() {
      status.set('connecting', 'Refreshing…');
      try {
        const response = await fetch('/api/refresh', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const info = await response.json();
        if (info.generatedAt !== data.generatedAt) { window.location.reload(); return; }
        status.set('live', 'Live');
        A.toast('Already up to date', { kind: 'info' });
      } catch {
        status.set('offline', 'Offline');
        A.toast('The gitstory server did not answer. Is it still running?', { kind: 'critical' });
      }
    }
    button.addEventListener('click', refresh);
    return { nodes: [status.el, button], refresh };
  }

  // Every chart card can show its data as a table; the view switch of the sub bar flips them all.
  const cards = [];
  function chartCard(parent, options) {
    const card = C.card(parent, options);
    cards.push(card);
    return card;
  }
  const withTable = () => cards.filter((card) => card.chart && card.chart.table);
  function showAll(mode) {
    withTable().forEach((card) => card.showTable(mode === 'tables'));
  }

  /* ── the Aurora shell: rail, top bar, sub bar and status bar ──────────────────────────── */

  const crumbs = (label) => [{ label: 'gitstory', href: '#story' }, repo.name, label];

  // A small fact in the sub bar. The label hides on narrow screens; the value stays.
  function pill(icon, label, value, title) {
    return el('span', { class: 'a-pill', title: title || null },
      A.icon(icon, { size: 14 }),
      el('span', { class: 'a-pill__label' }, label),
      value == null ? null : el('span', { class: 'a-pill__count' }, el('span', { class: 'gs-ellipsis' }, String(value))));
  }

  function contextPills() {
    const t = data.totals;
    const pills = [pill('git-branch', 'Branch', repo.detached ? 'detached' : repo.branch, `HEAD ${repo.head}`)];
    if (hasCommits) {
      pills.push(pill('git-commit', 'Commits', int(t.commits), `${plural(t.mergeCommits, 'merge commit')} included`));
      pills.push(pill('users', 'Authors', int(t.authors)));
      pills.push(pill('calendar', 'Span', shortSpan(t.spanDays), `${day(t.firstCommitAt)} → ${day(t.lastCommitAt)}`));
    }
    const chips = filterChips();
    if (chips.length) pills.push(pill('filter', 'Filtered', chips.length, chips.join(' · ')));
    return pills;
  }

  const liveControls = setupLive();
  const generated = data.generatedAt.replace('T', ' ').replace('Z', ' UTC');
  const shell = A.shell({
    brand: { name: 'gitstory', href: '#story', mark: 'git-commit' },
    rail: [
      sections.map(([id, label, icon]) => ({ id, label, icon, href: `#${id}`, active: id === 'story' })),
      'spacer',
      [
        { id: 'json', label: 'Download the metrics as JSON', icon: 'download', onClick: downloadJson },
        { id: 'palette', label: 'Command palette', icon: 'command', cmdk: true },
      ],
    ],
    crumbs: crumbs('Story'),
    actions: liveControls.nodes,
    views: hasCommits ? {
      ariaLabel: 'How the charts are shown',
      active: 'charts',
      items: [{ id: 'charts', label: 'Charts', icon: 'bar-chart' }, { id: 'tables', label: 'Tables', icon: 'list' }],
      onChange: showAll,
    } : null,
    subbarEnd: contextPills(),
    status: {
      start: [`gitstory ${data.generator.version}`, 'Generated offline · nothing was uploaded'],
      end: [data.schema, generated],
    },
  });

  // The page itself: one column of sections inside the container the shell made.
  const app = el('div', { class: 'gs-page' });
  shell.container.appendChild(app);
  app.addEventListener('click', (event) => {
    if (!shell.views || !event.target.closest || !event.target.closest('.a-card__actions .a-chip')) return;
    const all = withTable();
    const tables = all.filter((card) => card.body.hidden).length;
    if (tables === all.length) shell.views.select('tables', true);
    else if (tables === 0) shell.views.select('charts', true);
  });

  /* ── page building blocks ──────────────────────────────────────────────────────────────── */

  function section(id, title, lead) {
    const head = el('div', { class: 'a-pagehead gs-section__head' },
      el('div', null,
        el('h2', { class: 'a-h2', id: `${id}-title` }, title),
        lead ? el('p', { class: 'a-pagehead__sub gs-lead' }, lead) : null));
    const grid = el('div', { class: 'a-grid' });
    const root = el('section', { class: 'gs-section', id, 'aria-labelledby': `${id}-title` }, head, grid);
    app.appendChild(root);
    return grid;
  }

  function panel(parent, title, sub) {
    const body = el('div', { class: 'a-card__body gs-panel' });
    const card = el('section', { class: 'a-card a-reveal', 'aria-label': title },
      el('div', { class: 'a-card__head' },
        el('div', null, el('div', { class: 'a-card__title' }, title), sub ? el('div', { class: 'a-card__sub' }, sub) : null)),
      body);
    parent.appendChild(card);
    return body;
  }

  function notice(kind, title, lines) {
    return el('div', { class: 'a-card gs-notice a-reveal', role: 'note' },
      el('div', { class: 'gs-notice__head' }, A.badge(title, { kind })),
      el('ul', { class: 'gs-notice__list' }, lines.map((line) => el('li', null, line))));
  }

  /* ── story: title, key numbers, highlights ─────────────────────────────────────────────── */

  function renderStory() {
    const t = data.totals;
    const lead = t.commits
      ? `${plural(t.commits, 'commit')} by ${plural(t.authors, 'author')} over ${spanText(t.spanDays)}, from ${longDate(t.firstCommitAt)} to ${longDate(t.lastCommitAt)}.`
      : 'No commits matched the filters of this report.';
    // a long name is cut with an ellipsis; the title attribute keeps the whole text
    const tag = (text, hint) => el('span', { class: 'a-tag', title: hint ? `${hint}: ${text}` : text }, text);
    const meta = el('div', { class: 'a-row gs-meta' },
      tag(repo.detached ? 'detached HEAD' : `branch ${repo.branch}`),
      tag(`HEAD ${repo.head}`));
    if (repo.shallow) meta.appendChild(A.badge('shallow clone', { kind: 'warn', title: 'The history is truncated; the numbers cover only what was fetched.' }));
    filterChips().forEach((text) => meta.appendChild(tag(text, 'Filter applied to this report')));

    const root = el('section', { class: 'gs-story', id: 'story', 'aria-labelledby': 'story-title' },
      el('div', { class: 'a-hero gs-hero' },
        el('p', { class: 'a-greet' }, 'gitstory · repository report'),
        el('h1', { class: 'a-headline', id: 'story-title', title: repo.name }, repo.name),
        el('p', { class: 'a-lede' }, lead),
        meta));
    app.appendChild(root);

    if (data.warnings.length && t.commits) root.appendChild(notice('warn', 'Heads up', data.warnings));
    if (!t.commits) {
      const empty = el('div', { class: 'a-card' });
      root.appendChild(empty);
      A.empty(empty, { title: 'Nothing to show', text: data.warnings[0] || 'Try a wider date range or another branch.', icon: 'search' });
      return false;
    }

    const kpis = el('div', { class: 'gs-kpis', role: 'group', 'aria-label': 'Key figures' });
    root.appendChild(kpis);
    const weekly = data.activity.byWeek.map((w) => w.commits).slice(-26);
    const hero = A.stat(cell(kpis, 'gs-kpi-hero'), {
      label: 'Commits', value: t.commits, format: 'int', hero: true,
      hint: `${plural(t.mergeCommits, 'merge commit')} included · ${t.commitsPerActiveDay} per active day`,
    });
    if (weekly.length > 1) {
      const trend = el('div', { class: 'gs-hero-trend' }, el('div', { class: 'a-stat__hint' }, `Commits per week, last ${weekly.length} weeks`));
      // about 13px a bar: a short history gets a small trend, not a few wide blocks
      C.spark(trend, { values: weekly, width: Math.min(340, 8 + weekly.length * 13), height: 64, kind: 'bars' });
      hero.el.querySelector('.a-stat__row').appendChild(trend);
    }
    const tiles = [
      { label: 'Authors', value: t.authors, hint: `bus factor ${data.busFactor.value} (a heuristic)` },
      { label: 'Active days', value: t.activeDays, hint: `${pct(t.spanDays ? t.activeDays / t.spanDays : 0)} of the days in the range` },
      {
        label: 'Longest streak', value: data.streaks.longest,
        hint: data.streaks.longest ? 'days in a row' : 'no streak',
        rich: data.streaks.longest ? ['days in a row, ', nowrap(day(data.streaks.longestFrom)), ' → ', nowrap(day(data.streaks.longestTo))] : null,
      },
      { label: 'Lines changed', value: t.additions + t.deletions, format: 'compact', hint: `+${int(t.additions)} / −${int(t.deletions)}` },
    ];
    tiles.forEach((tile) => statTile(cell(kpis, 'gs-kpi-small'), tile));
    return true;
  }

  const FACT_ICON = {
    currentStreak: 'bolt', busiestDay: 'calendar', chronotype: 'clock', topAuthor: 'user', busFactor: 'shield',
    biggestCommit: 'git-commit', topLanguage: 'code', hotFile: 'file', longestGap: 'pause', commitStyle: 'hash',
    releases: 'tag', signatureWord: 'message',
  };
  const IN_KPIS = new Set(['commits', 'activeDays', 'longestStreak']);

  function renderFacts() {
    const facts = data.highlights.filter((h) => !IN_KPIS.has(h.id));
    if (!facts.length) return;
    // the cards below are level 3 headings: they need a level 2 above them, which only a screen reader meets
    const story = document.getElementById('story');
    story.appendChild(el('h2', { class: 'a-sr-only', id: 'highlights-title' }, 'Highlights'));
    const grid = el('div', { class: 'gs-facts', role: 'list', 'aria-labelledby': 'highlights-title' });
    facts.forEach((fact) => {
      const numeric = typeof fact.value === 'number';
      const value = el('div', { class: `gs-fact__value${numeric ? '' : ' gs-fact__value--text'}` }, numeric ? '0' : String(fact.value));
      const card = el('article', { class: 'a-card gs-fact a-reveal', role: 'listitem' },
        el('h3', { class: 'gs-fact__title' }, A.icon(FACT_ICON[fact.id] || 'sparkle', { size: 14 }), fact.title),
        value,
        el('p', { class: 'gs-fact__caption' }, fact.caption));
      grid.appendChild(card);
      if (numeric) onVisible(card, () => A.countUp(value, fact.value, { format: 'int', duration: 1100 }));
    });
    story.appendChild(grid);
  }

  /* ── activity ──────────────────────────────────────────────────────────────────────────── */

  function peakOf(rows, key) {
    return rows.reduce((best, row) => (row.commits > best.commits ? row : best), { commits: -1, [key]: '' });
  }

  function renderActivity() {
    const t = data.totals;
    const months = data.activity.byMonth;
    const peak = peakOf(months, 'month');
    const share = t.spanDays ? t.activeDays / t.spanDays : 0;
    const lead = `Commits landed on ${int(t.activeDays)} of ${plural(t.spanDays, 'day')} (${pct(share)}).` +
      (peak.commits > 0 ? ` The busiest month was ${monthText.format(new Date(+peak.month.slice(0, 4), +peak.month.slice(5, 7) - 1, 1))}, with ${plural(peak.commits, 'commit')}.` : '');
    const grid = section('activity', 'Activity', lead);

    const cal = data.activity.calendar;
    chartCard(cell(grid, 'a-span-12'), {
      title: 'Activity calendar',
      subtitle: `Commits per day up to ${cal.to} · ${plural(cal.total, 'commit')} in the last ${cal.weeks} weeks`,
      render: (body) => C.heatmap(body, {
        kind: 'calendar', unit: 'commits', end: cal.to,
        cells: cal.days.map((d) => ({ date: d.date, value: d.commits })),
        ariaLabel: 'Commits per day over the last year',
      }),
    });

    renderOverTime(cell(grid, 'a-span-8'));
    renderRhythm(cell(grid, 'a-span-4'));

    const wh = data.activity.weekdayHour;
    const matrix = [];
    wh.matrix.forEach((row, r) => row.forEach((value, c) => matrix.push({ row: r, col: c, value })));
    chartCard(cell(grid, 'a-span-12'), {
      title: 'Weekday and hour',
      subtitle: 'Commits by the author’s local time',
      render: (body) => C.heatmap(body, {
        kind: 'matrix', unit: 'commits', rows: wh.weekdays, cols: Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0')),
        cells: matrix, rowLabel: 'Weekday', colLabel: 'Hour', ariaLabel: 'Commits by weekday and hour',
      }),
    });
  }

  function renderOverTime(host) {
    const a = data.activity;
    const views = {
      month: { label: 'Monthly', rows: a.byMonth, when: (r) => new Date(+r.month.slice(0, 4), +r.month.slice(5, 7) - 1, 1), text: (d) => monthText.format(d), axis: 'Month' },
      week: { label: 'Weekly', rows: a.byWeek, when: (r) => localDate(r.start), text: (d) => `Week of ${dateText.format(d)}`, axis: 'Week' },
      year: { label: 'Yearly', rows: a.byYear, when: (r) => new Date(r.year, 0, 1), text: (d) => String(d.getFullYear()), axis: 'Year' },
    };
    // a line through one or two points says nothing: such a resolution is not offered
    const available = Object.keys(views).filter((k) => views[k].rows.length >= 3);
    const options = (id) => {
      const view = views[id];
      return {
        series: [{ id: 'commits', name: 'Commits', color: 1, values: view.rows.map((r) => ({ x: view.when(r), y: r.commits })) }],
        x: { type: 'time', label: view.axis, tooltipFormat: (ms) => view.text(new Date(ms)) },
        y: { label: 'Commits', format: fmt.compact },
      };
    };
    const tabsHost = el('div');
    let chart = null;
    const holder = chartCard(host, {
      title: 'Commits over time',
      subtitle: 'Per calendar month, week or year',
      actions: [tabsHost],
      render: (body) => {
        if (!available.length) {
          A.empty(body, { title: 'Not enough history yet', text: 'A trend needs commits in at least three different months, weeks or years.', icon: 'clock' });
          return null;
        }
        chart = C.line(body, Object.assign({ area: true, height: 280, legend: 'never', ariaLabel: 'Commits over time' }, options(available[0])));
        return chart;
      },
    });
    if (available.length > 1) {
      A.tabs(tabsHost, {
        ariaLabel: 'Time resolution',
        tabs: available.map((id) => ({ id, label: views[id].label })),
        active: available[0],
        onChange: (id) => { chart.update(options(id)); if (holder.body.hidden) holder.showTable(true); },
      });
    }
  }

  function renderRhythm(host) {
    const ch = data.chronotype;
    const body = panel(host, 'Daily rhythm', 'Local time of the author');
    body.appendChild(el('div', { class: 'gs-rhythm__label' }, ch.label));
    const best = ch.windows.find((w) => w.id === ch.type);
    body.appendChild(el('p', { class: 'a-muted gs-rhythm__caption' },
      best ? `${pct(best.share)} of commits land between ${hourText(best.from)} and ${lastMinute(best.to)}.` : 'Too few commits to call it.'));
    A.segbar(body, {
      tall: true, valueFormat: int,
      parts: ch.windows.map((w) => ({ name: `${w.label} ${hourText(w.from)}–${hourText(w.to % 24)}`, value: w.commits })),
    });
    const perDay = data.activity.weekdayHour.matrix.map(add);
    const busiestDay = perDay.indexOf(Math.max.apply(null, perDay));
    const weekend = (perDay[5] + perDay[6]) / Math.max(1, add(perDay));
    A.kv(body, [
      ['Peak hour', ch.peakHour == null ? '–' : `${hourText(ch.peakHour)}–${hourText((ch.peakHour + 1) % 24)}`],
      ['Busiest weekday', `${data.activity.weekdayHour.weekdays[busiestDay]} (${plural(perDay[busiestDay], 'commit')})`],
      ['On weekends', pct(weekend)],
    ]);
  }

  /* ── people ────────────────────────────────────────────────────────────────────────────── */

  function renderPeople() {
    const au = data.authors;
    const top = au.items[0];
    const lead = `${plural(au.total, 'author')} contributed.` +
      (top && !top.other ? ` ${top.name} wrote ${pct(top.share)} of the commits.` : '');
    const grid = section('people', 'People', lead);

    chartCard(cell(grid, 'a-span-8'), {
      title: 'Top authors',
      subtitle: au.items.some((a) => a.other) ? `The ${au.limit} most active, then everyone else` : 'Commits per author',
      render: (body) => C.bars(body, {
        orientation: 'horizontal', valueLabel: 'Commits', labelHeader: 'Author', valueFormat: int, title: 'Top authors',
        data: au.items.map((a, i) => ({
          id: i,
          label: a.other ? `Others (${int(a.members)})` : a.name,
          value: a.commits,
          sub: `${pct(a.share, 1)} of commits · +${int(a.additions)} / −${int(a.deletions)} lines · ${plural(a.activeDays, 'active day')} · ${day(a.firstCommitAt)} → ${day(a.lastCommitAt)}`,
        })),
      }),
    });

    const bus = data.busFactor;
    const body = panel(cell(grid, 'a-span-4'), 'Bus factor', 'A rough heuristic, not a verdict');
    const ring = el('div', { class: 'gs-bus__ring' });
    body.appendChild(ring);
    C.ring(ring, {
      value: bus.coverage * 100, max: 100, size: 156, label: 'Commits covered by the top authors',
      sublabel: `${plural(bus.value, 'author')}`, format: (v) => `${Math.round(v)}%`, thresholds: [],
    });
    body.appendChild(el('p', { class: 'gs-bus__names' }, bus.authors.join(', ')));
    body.appendChild(el('p', { class: 'a-muted gs-note' }, bus.note));
  }

  /* ── code ──────────────────────────────────────────────────────────────────────────────── */

  // The end of a path is the informative part, and the chart cuts labels at the end.
  const shortPaths = (files) => files.map((f) => f.path.split('/').slice(-2).join('/'));

  function renderCode() {
    const langs = data.languages;
    const hot = data.hotspots;
    const lead = langs.items.length && !langs.items[0].other
      ? `${langs.items[0].name} accounts for ${pct(langs.items[0].share)} of the changed lines.`
      : 'Changed lines per file type and per file; lockfiles, bundles and vendored code are left out.';
    const grid = section('code', 'Code', lead);

    const labels = shortPaths(hot.files);
    chartCard(cell(grid, 'a-span-7'), {
      title: 'Hottest files',
      subtitle: `Changed lines (added + deleted) · files touched at least ${hot.minCommits} times`,
      render: (body) => C.bars(body, {
        orientation: 'horizontal', valueLabel: 'Changed lines', labelHeader: 'File', valueFormat: int, title: 'Hottest files',
        emptyText: 'No file statistics in this history',
        data: hot.files.map((f, i) => ({
          id: i, label: labels[i], value: f.changes,
          sub: `${f.path} · ${plural(f.commits, 'commit')} · +${int(f.additions)} / −${int(f.deletions)} · ${f.language}`,
        })),
      }),
    });

    const side = cell(grid, 'a-span-5 gs-side');
    if (langs.items.length > 6) {
      chartCard(side, {
        title: 'Languages',
        subtitle: 'Share of changed lines, by file type',
        render: (body) => C.bars(body, {
          orientation: 'horizontal', valueLabel: 'Changed lines', labelHeader: 'Language', valueFormat: int, title: 'Languages',
          data: langs.items.map((l, i) => ({ id: i, label: l.name, value: l.changes, sub: pct(l.share, 1) })),
        }),
      });
    } else {
      const langBody = panel(side, 'Languages', 'Share of changed lines, by file type');
      if (!langs.items.length) A.empty(langBody, { title: 'No language data', text: 'No file statistics in this history.', icon: 'code' });
      else A.segbar(langBody, { tall: true, valueFormat: int, parts: langs.items.map((l) => ({ name: l.name, value: l.changes })) });
    }

    const sz = data.commitSize;
    const median = sz.buckets.findIndex((b) => b.max === null || sz.median <= b.max);
    chartCard(side, {
      title: 'Commit size',
      subtitle: `${plural(sz.commits, 'commit')} by changed lines. The median is ${int(sz.median)} (highlighted), the 90th percentile ${int(sz.p90)}.`,
      render: (body) => C.bars(body, {
        orientation: 'vertical', height: 200, valueLabel: 'Commits', labelHeader: 'Changed lines', valueFormat: int,
        title: 'Commit size distribution', highlight: median < 0 ? undefined : median,
        data: sz.buckets.map((b, i) => ({ id: i, label: b.id.replace('-', '–'), value: b.commits, sub: b.max === null ? `${int(b.min)} or more lines` : `${int(b.min)} to ${int(b.max)} lines` })),
      }),
    });
  }

  /* ── messages ──────────────────────────────────────────────────────────────────────────── */

  function renderMessages() {
    const cv = data.conventional;
    const words = data.words;
    const lead = cv.considered
      ? `${pct(cv.share)} of commit subjects follow Conventional Commits${cv.breaking ? `, ${plural(cv.breaking, 'commit')} marked as breaking` : ''}.`
      : 'There are no commit messages to read.';
    const grid = section('messages', 'Messages', lead);

    chartCard(cell(grid, 'a-span-6'), {
      title: 'Commit types',
      subtitle: `Prefix of ${plural(cv.considered, 'subject')} (merge commits left out)`,
      render: (body) => C.bars(body, {
        orientation: 'horizontal', valueLabel: 'Commits', labelHeader: 'Type', valueFormat: int, title: 'Commit types',
        emptyText: 'No commit subjects',
        data: cv.types.map((t, i) => ({ id: i, label: t.type, value: t.commits, sub: pct(t.share, 1) })),
      }),
    });
    chartCard(cell(grid, 'a-span-6'), {
      title: 'Words in commit subjects',
      subtitle: `${plural(words.unique, 'distinct word')} in ${int(words.tokens)} · stopwords and numbers left out`,
      render: (body) => C.bars(body, {
        orientation: 'horizontal', valueLabel: 'Uses', labelHeader: 'Word', valueFormat: int, title: 'Words in commit subjects',
        emptyText: 'No words to count',
        data: words.top.slice(0, 12).map((w, i) => ({ id: i, label: w.word, value: w.count })),
      }),
    });
  }

  /* ── releases ──────────────────────────────────────────────────────────────────────────── */

  function renderReleases() {
    const rel = data.releases;
    const lead = rel.count
      ? `${plural(rel.count, 'tag')}${rel.meanIntervalDays != null ? `, one every ${rel.meanIntervalDays} days on average` : ''}. The latest is ${rel.latest.name}, ${plural(rel.latest.daysAgo, 'day')} ago.`
      : 'Releases are read from git tags.';
    const grid = section('releases', 'Releases', lead);
    if (!rel.count) {
      A.empty(cell(grid, 'a-span-12 a-card gs-empty'), { title: 'No tags found', text: 'Tag a commit (for example git tag v1.0.0) and the releases will show up here.', icon: 'tag' });
      return;
    }
    const tiles = [
      { label: 'Releases', value: rel.count, format: 'int', hint: `${rel.annotated} annotated` },
      { label: 'Mean interval', value: rel.meanIntervalDays == null ? 0 : rel.meanIntervalDays, format: (v) => `${fmt.number(v, 1)} d`, hint: 'days between tags' },
      { label: 'Median interval', value: rel.medianIntervalDays == null ? 0 : rel.medianIntervalDays, format: (v) => `${fmt.number(v, 1)} d`, hint: 'less affected by outliers' },
      { label: 'Latest', value: rel.latest.daysAgo, format: (v) => `${int(v)} d ago`, hint: rel.latest.name, rich: [`${rel.latest.name} · `, nowrap(day(rel.latest.date))] },
    ];
    tiles.forEach((tile) => statTile(cell(grid, 'gs-tile'), tile));

    const shown = rel.items.filter((r) => r.daysSincePrevious != null).slice(-24);
    chartCard(cell(grid, 'a-span-8'), {
      title: 'Days between releases',
      subtitle: shown.length < rel.count - 1 ? `The latest ${shown.length} of ${int(rel.count - 1)} intervals, oldest first` : 'One bar per tag after the first, oldest first',
      render: (body) => C.bars(body, {
        orientation: 'vertical', height: 340, valueLabel: 'Days since the previous tag', labelHeader: 'Tag', valueFormat: (v) => fmt.number(v, Number.isInteger(v) ? 0 : 1),
        title: 'Days between releases',
        data: shown.map((r, i) => ({ id: i, label: r.name, value: r.daysSincePrevious, sub: `${day(r.date)}${r.annotated ? ' · annotated' : ''}` })),
      }),
    });

    const tableCard = el('section', { class: 'a-card a-card--flush a-span-4 a-reveal', 'aria-label': 'Releases' },
      el('div', { class: 'a-card__head' }, el('div', null, el('div', { class: 'a-card__title' }, 'All tags'), el('div', { class: 'a-card__sub' }, 'Newest first'))));
    grid.appendChild(tableCard);
    A.table(tableCard, {
      dense: true, pageSize: 8, search: { placeholder: 'Search tags…', keys: ['name'] }, sort: { key: 'date', dir: 'desc' },
      columns: [
        { key: 'name', label: 'Tag', mono: true },
        { key: 'date', label: 'Date', format: (v) => day(v) },
        { key: 'daysSincePrevious', label: 'Gap', align: 'right', format: (v) => (v == null ? '–' : `${fmt.number(v, Number.isInteger(v) ? 0 : 1)} d`) },
      ],
      rows: rel.items,
    });
  }

  /* ── README card ───────────────────────────────────────────────────────────────────────── */

  const SNIPPET = '![gitstory card](card.svg)';

  function renderCard() {
    const grid = section('card', 'README card', 'An animated SVG for your README. It is pure SVG and CSS, so GitHub renders it in an <img> without scripts or network requests.');
    const sources = document.getElementById('card-sources');
    const img = el('img', { class: 'gs-card-img', alt: `gitstory card for ${repo.name}`, width: '495', height: '195' });
    const apply = () => { img.src = sources.getAttribute(A.theme.get() === 'light' ? 'data-light' : 'data-dark'); };
    apply();
    document.addEventListener('aurora:theme', apply);
    const preview = panel(cell(grid, 'a-span-7'), 'Preview', 'Follows the theme of this page; the card itself adapts to the viewer');
    preview.appendChild(el('div', { class: 'gs-card-frame' }, img));

    const how = panel(cell(grid, 'a-span-5'), 'Add it to your README', 'Generate it again whenever you like, for example in CI');
    how.appendChild(el('p', { class: 'a-muted gs-note' }, '1. Write the card next to your README'));
    A.code(how, 'gitstory card -o card.svg');
    how.appendChild(el('p', { class: 'a-muted gs-note' }, '2. Reference it from Markdown'));
    A.code(how, SNIPPET);
    const flag = (text) => el('code', { class: 'a-inline-code' }, text);
    how.appendChild(el('p', { class: 'a-muted gs-note' }, 'Pin the colours with ', flag('--theme dark'), ' or ', flag('--theme light'), ', rename it with ', flag('--title'), ' and change the heatmap with ', flag('--weeks'), '.'));
  }

  /* ── command palette ───────────────────────────────────────────────────────────────────── */

  function setupCommands(refresh) {
    const go = ([id, label]) => ({ id: `go-${id}`, label: `Go to ${label}`, group: 'Navigate', hint: 'section', run: () => goTo(id) });
    const commands = sections.map(go);
    commands.push({ id: 'theme', label: 'Toggle light / dark theme', group: 'Actions', keywords: 'appearance mode', run: () => A.theme.toggle() });
    commands.push({ id: 'json', label: 'Download the metrics as JSON', group: 'Actions', keywords: 'export save data', run: downloadJson });
    if (hasCommits) {
      commands.push({ id: 'charts', label: 'Show the charts', group: 'Actions', keywords: 'view graphs', run: () => { shell.views.select('charts'); } });
      commands.push({ id: 'tables', label: 'Show the data as tables', group: 'Actions', keywords: 'view rows numbers', run: () => { shell.views.select('tables'); } });
      commands.push({ id: 'snippet', label: 'Copy the README snippet for the card', group: 'Actions', keywords: 'markdown badge image', run: () => A.copy(SNIPPET, { label: 'Snippet copied' }) });
    }
    if (refresh) commands.push({ id: 'refresh', label: 'Refresh the data from the repository', group: 'Actions', keywords: 'reload update', run: refresh });
    A.cmdk.register(commands);
  }

  /* ── the rail, the breadcrumbs and the view follow the scroll ──────────────────────────── */

  // Opens a section: the address changes (so the browser scrolls there), or the page just scrolls when it already points there.
  function goTo(id) {
    const target = document.getElementById(id);
    if (!target) return;
    if (window.location.hash === `#${id}`) target.scrollIntoView();
    else window.location.hash = `#${id}`;
  }

  function trackSections() {
    const targets = sections.map(([id]) => document.getElementById(id));
    let current = sections[0][0];
    let pinned = false; // a destination that was just chosen wins until the page has finished scrolling to it
    let release = 0;

    function show(index) {
      const [id, label] = sections[index];
      if (id === current) return;
      current = id;
      shell.setActive(id);
      shell.setCrumbs(crumbs(label));
    }
    function settle() {
      window.clearTimeout(release);
      release = window.setTimeout(() => { pinned = false; }, 160);
    }
    function pin(id) {
      const index = sections.findIndex((entry) => entry[0] === id);
      if (index < 0) return;
      pinned = true;
      show(index);
      settle();
    }
    const follow = A.throttle(() => {
      if (pinned) { settle(); return; } // still scrolling towards the chosen section
      let index = 0;
      targets.forEach((target, i) => { if (target && target.getBoundingClientRect().top < window.innerHeight * 0.4) index = i; });
      const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      show(atEnd ? targets.length - 1 : index);
    }, 80);

    window.addEventListener('scroll', follow, { passive: true });
    window.addEventListener('resize', follow);
    window.addEventListener('hashchange', () => pin(window.location.hash.slice(1)));
    document.addEventListener('click', (event) => {
      const link = event.target.closest ? event.target.closest('a[href^="#"]') : null;
      if (link) pin(link.getAttribute('href').slice(1));
    });
    follow();
  }

  /* ── go ────────────────────────────────────────────────────────────────────────────────── */

  const hasStory = renderStory();
  if (hasStory) {
    renderFacts();
    renderActivity();
    renderPeople();
    renderCode();
    renderMessages();
    renderReleases();
    renderCard();
    trackSections();
  }
  setupCommands(liveControls.refresh);
  A.reveal(app);

  // The page was opened on a section (#people): the sections did not exist yet when the browser looked.
  const opened = sections.find(([id]) => `#${id}` === window.location.hash);
  if (opened) document.getElementById(opened[0]).scrollIntoView({ behavior: 'instant' });
})();
