window.RadioMetrics = (() => {
  function el(tag, text = '', className = '') {
    const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
  }
  function bars(items, label, value) {
    const chart = el('div', '', 'metric-bars');
    const max = Math.max(1, ...items.map(value));
    for (const item of items) {
      const row = el('div', '', 'metric-bar-row');
      const name = label(item); const count = value(item);
      const meter = el('meter'); meter.min = 0; meter.max = max; meter.value = count;
      meter.setAttribute('aria-label', `${name} : ${count}`);
      row.append(el('span', name), meter, el('strong', String(count)));
      chart.append(row);
    }
    if (!items.length) chart.append(el('p', window.RadioI18n.t("ui.4e14defa0f18"), 'hint'));
    return chart;
  }
  function table(headers, rows) {
    const wrap = el('div', '', 'table-wrap'); const table = el('table');
    const head = el('thead'); const tr = el('tr');
    headers.forEach(name => { const cell = el('th', name); cell.scope = 'col'; tr.append(cell); });
    head.append(tr); const body = el('tbody');
    for (const cells of rows) {
      const row = el('tr');
      for (const value of cells) { const td = el('td'); td.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); row.append(td); }
      body.append(row);
    }
    table.append(head, body); wrap.append(table);
    if (!rows.length) wrap.append(el('p', window.RadioI18n.t("ui.4e14defa0f18"), 'hint'));
    return wrap;
  }
  function hourly(items) {
    const chart = el('div', '', 'hourly-chart');
    const max = Math.max(1, ...items.map(item => item.plays));
    for (const item of items) {
      const column = el('div', '', 'hourly-column');
      const meter = el('meter'); meter.min = 0; meter.max = max; meter.value = item.plays;
      meter.title = window.RadioI18n.t("ui.c85e2a475367", {p0: (item.hour), p1: (item.plays)}); meter.setAttribute('aria-label', meter.title);
      column.append(meter, el('span', item.hour % 3 === 0 ? String(item.hour) : '')); chart.append(column);
    }
    return chart;
  }
  return {el, bars, table, hourly};
})();
window.RadioPages = window.RadioPages || {};
window.RadioPages.stats = () => {
  const {el, bars, table} = window.RadioMetrics;
  const root = document.querySelector('#stats-root');
  const controller = new AbortController(); let generation = 0;
  const form = root.querySelector('form');
  const period = root.querySelector('#repetition-hours');
  const pages = {};
  const names = new Map([...form.elements.station_id.options].filter(o => o.value).map(o => [Number(o.value), o.textContent]));
  function trackLink(row) {
    const button = el('button', `${row.artist || window.RadioI18n.t("ui.133df509234a")} — ${row.title}`, 'cell-link');
    button.type = 'button'; button.onclick = () => window.TrackCatalog.open(row, {showHourly: true}); return button;
  }
  async function summary(current = generation) {
    const status = root.querySelector('#stats-summary-status');
    const cards = root.querySelector('#stats-summary-cards');
    const top = root.querySelector('#stats-summary-top');
    cards.replaceChildren(); top.replaceChildren(); status.textContent = window.RadioI18n.t("ui.c737b3c96f52");
    const params = window.MusicFilters.apply(new URLSearchParams(new FormData(form)));
    params.set('hours', period.value);
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    try {
      const response = await window.MusicFilters.fetch(`/api/stats/summary?${params}`, {signal: controller.signal});
      if (!response.ok) throw new Error(window.RadioI18n.t("ui.423b18ec505d"));
      const data = await response.json();
      if (current !== generation || controller.signal.aborted) return;
      status.textContent = window.RadioI18n.t("ui.653e418cd193", {p0: (data.total ? window.RadioI18n.t("ui.eb209352b8d1") : window.RadioI18n.t("ui.d608b8805136")), p1: (new Date(data.since).toLocaleString((window.RadioI18n?.locale || 'fr-FR'))), p2: (new Date(data.until).toLocaleString((window.RadioI18n?.locale || 'fr-FR')))});
      for (const [label, value, detail] of [
        [window.RadioI18n.t("ui.7da8e332a6ef"), data.total, window.RadioI18n.t("ui.68294a9a19aa")],
        [window.RadioI18n.t("ui.555672b24471"), data.unique_tracks, window.RadioI18n.t("ui.8c9982dbf2c8")],
        [window.RadioI18n.t("ui.4b1e0fbe4b96"), data.stations, window.RadioI18n.t("ui.68294a9a19aa")],
        [window.RadioI18n.t("ui.330972f0da1f"), data.total ? `${data.genre_coverage} %` : '—', window.RadioI18n.t("ui.52dad73107d6", {p0: (data.known_genres), p1: (data.total)})],
      ]) {
        const card = el('article', '', 'stats-summary-card');
        card.append(el('span', label), el('strong', typeof value === 'number' ? new Intl.NumberFormat((window.RadioI18n?.locale || 'fr-FR')).format(value) : value), el('small', detail)); cards.append(card);
      }
      top.append(el('h3', window.RadioI18n.t("ui.6f275afa918c", {p0: (period.value)})));
      if (data.top) {
        const row = el('div', '', 'stats-top-track');
        if (data.top.metadata?.cover_url) {
          const img = el('img'); img.src = data.top.metadata.cover_url; img.alt = ''; img.loading = 'lazy'; img.onerror = () => img.remove(); row.append(img);
        }
        row.append(trackLink(data.top), el('span', window.RadioI18n.t("ui.143ab1c9f90b", {p0: (data.top.plays)}))); top.append(row);
      } else top.append(el('p', window.RadioI18n.t("ui.4fe68628cf6c"), 'hint'));
      top.append(el('p', window.RadioI18n.t("ui.d312ed2dde18", {p0: (new Date(data.top_since).toLocaleString((window.RadioI18n?.locale || 'fr-FR'))), p1: (new Date(data.until).toLocaleString((window.RadioI18n?.locale || 'fr-FR')))}), 'hint'));
    } catch (error) { if (error.name !== 'AbortError' && current === generation) status.textContent = error.message; }
  }
  async function load(kind, current = generation) {
    const section = root.querySelector(`[data-metric="${kind}"]`);
    const status = section.querySelector('[role="status"]'); const output = section.querySelector('.metric-output');
    const previous = section.querySelector('[data-previous]'); const next = section.querySelector('[data-next]');
    const params = window.MusicFilters.apply(new URLSearchParams(new FormData(form)));
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    params.set('hours', period.value); params.set('page', String(pages[kind] || 1));
    output.replaceChildren(); status.textContent = window.RadioI18n.t("ui.66474911d953"); previous.disabled = true; next.disabled = true;
    try {
      if (kind === 'offsets' && (!params.get('station_a') || params.get('station_a') === params.get('station_b'))) {
        output.replaceChildren(); status.textContent = window.RadioI18n.t("ui.4564454fbf9b"); return;
      }
      const response = await window.MusicFilters.fetch(`/api/stats/${kind}?${params}`, {signal: controller.signal});
      if (!response.ok) throw new Error(window.RadioI18n.t("ui.ec260dcc09d1"));
      const data = await response.json();
      if (current !== generation || controller.signal.aborted) return;
      const interval = window.RadioI18n.t("ui.73798f51e5ae", {p0: (new Date(data.since).toLocaleString((window.RadioI18n?.locale || 'fr-FR'))), p1: (new Date(data.until).toLocaleString((window.RadioI18n?.locale || 'fr-FR')))});
      status.textContent = interval;
      let node;
      if (kind === 'repetitions') node = table([window.RadioI18n.t("ui.78e7920010aa"), window.RadioI18n.t("ui.2a7dfeb2f2b0"), window.RadioI18n.t("ui.4413ab069b7c"), window.RadioI18n.t("ui.e0e5f23e11b7")], data.items.map(row => {
        const music = el('div', '', 'stats-repeat-track');
        if (row.metadata?.cover_url) { const img = el('img'); img.src = row.metadata.cover_url; img.alt = ''; img.loading = 'lazy'; img.onerror = () => img.remove(); music.append(img); }
        music.append(trackLink(row));
        return [music, row.station_name, row.plays, row.average_seconds == null ? window.RadioI18n.t("ui.65e8907706fa") : `${Math.round(row.average_seconds / 60)} min`];
      }));
      if (kind === 'diversity') node = table([window.RadioI18n.t("ui.2a7dfeb2f2b0"), window.RadioI18n.t("ui.7e391bce2b02"), window.RadioI18n.t("ui.4413ab069b7c")], data.items.map(row => [row.station_name, row.unique_tracks, row.plays]));
      if (kind === 'overlap') {
        const pairs = new Map(data.items.map(pair => [`${pair.station_a}:${pair.station_b}`, pair]));
        node = table([window.RadioI18n.t("ui.a65c86b67354"), ...data.stations.map(s => `${s.name} (${s.unique_tracks})`)], data.stations.map(a => [a.name, ...data.stations.map(b => {
          const pair = pairs.get(`${a.id}:${b.id}`);
          return pair.percent == null ? window.RadioI18n.t("ui.10bf33d28104") : `${pair.percent} % · ${pair.shared}/${pair.union}`;
        })]));
      }
      if (kind === 'genres') {
        node = bars(data.items, row => row.genre, row => row.plays);
        status.textContent += window.RadioI18n.t("ui.ef4462bc3fe5", {p0: (data.coverage), p1: (data.total)});
      }
      if (kind === 'offsets') node = table([window.RadioI18n.t("ui.78e7920010aa"), window.RadioI18n.t("ui.cdefb32a3d84"), window.RadioI18n.t("ui.2db5ee014424"), window.RadioI18n.t("ui.c67a79e290c8")], data.items.map(row => {
        const first = row.seconds >= 0 ? Number(params.get('station_a')) : Number(params.get('station_b'));
        const second = row.seconds >= 0 ? Number(params.get('station_b')) : Number(params.get('station_a'));
        return [trackLink(row), `${names.get(first)} · ${new Date(row.seconds >= 0 ? row.at_a : row.at_b).toLocaleTimeString((window.RadioI18n?.locale || 'fr-FR'))}`, `${names.get(second)} · ${new Date(row.seconds >= 0 ? row.at_b : row.at_a).toLocaleTimeString((window.RadioI18n?.locale || 'fr-FR'))}`, row.seconds === 0 ? window.RadioI18n.t("ui.02ff944bd107") : `${Math.abs(row.seconds)} s`];
      }));
      output.replaceChildren(node);
      section.dataset.loadedGeneration = String(current);
      previous.disabled = (pages[kind] || 1) <= 1; next.disabled = !data.has_more;
      section.querySelector('[data-page]').textContent = window.RadioI18n.t("ui.dfeb8707a5c4", {p0: (pages[kind] || 1)});
      section.querySelector('.pagination').hidden = ['genres', 'overlap'].includes(kind);
    } catch (error) {
      if (error.name !== 'AbortError' && current === generation) status.textContent = error.message;
    }
  }
  function refresh() {
    generation++;
    root.querySelector('[data-metric="repetitions"] [data-metric-window]').textContent = window.RadioI18n.t("ui.ac974d046792", {p0: (period.value)});
    summary();
    for (const section of root.querySelectorAll('[data-metric]')) {
      pages[section.dataset.metric] = 1;
      if (section.open) load(section.dataset.metric);
      else { section.querySelector('.metric-output').replaceChildren(); section.querySelector('[role="status"]').textContent = window.RadioI18n.t("ui.26f72a5bbaac"); }
    }
  }
  for (const section of root.querySelectorAll('[data-metric]')) {
    const kind = section.dataset.metric;
    section.addEventListener('toggle', () => { if (section.open && section.dataset.loadedGeneration !== String(generation)) load(kind); });
    section.querySelector('[data-previous]').onclick = () => { pages[kind]--; load(kind); };
    section.querySelector('[data-next]').onclick = () => { pages[kind] = (pages[kind] || 1) + 1; load(kind); };
  }
  form.onsubmit = event => { event.preventDefault(); refresh(); };
  form.onchange = refresh;
  const onFilters = () => refresh(); window.addEventListener('radio:filterschange', onFilters); window.addEventListener('radio:catalogchange', onFilters);
  root.querySelector('[data-refresh]').onclick = refresh;
  refresh();
  return () => { controller.abort(); generation++; window.removeEventListener('radio:filterschange', onFilters); window.removeEventListener('radio:catalogchange', onFilters); };
};
