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
    if (!items.length) chart.append(el('p', 'Aucune donnée sur cette période.', 'hint'));
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
    if (!rows.length) wrap.append(el('p', 'Aucune donnée sur cette période.', 'hint'));
    return wrap;
  }
  function hourly(items) {
    const chart = el('div', '', 'hourly-chart');
    const max = Math.max(1, ...items.map(item => item.plays));
    for (const item of items) {
      const column = el('div', '', 'hourly-column');
      const meter = el('meter'); meter.min = 0; meter.max = max; meter.value = item.plays;
      meter.title = `${item.hour} h : ${item.plays} détections`; meter.setAttribute('aria-label', meter.title);
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
    const button = el('button', `${row.artist || 'Artiste inconnu'} — ${row.title}`, 'cell-link');
    button.type = 'button'; button.onclick = () => window.TrackCatalog.open(row, {showHourly: true}); return button;
  }
  async function load(kind, current = generation) {
    const section = root.querySelector(`[data-metric="${kind}"]`);
    const status = section.querySelector('[role="status"]'); const output = section.querySelector('.metric-output');
    const previous = section.querySelector('[data-previous]'); const next = section.querySelector('[data-next]');
    const params = window.MusicFilters.apply(new URLSearchParams(new FormData(form)));
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    params.set('hours', period.value); params.set('page', String(pages[kind] || 1));
    output.replaceChildren(); status.textContent = 'Chargement…'; previous.disabled = true; next.disabled = true;
    try {
      if (kind === 'offsets' && (!params.get('station_a') || params.get('station_a') === params.get('station_b'))) {
        output.replaceChildren(); status.textContent = 'Choisissez deux radios différentes pour comparer leurs détections.'; return;
      }
      const response = await window.MusicFilters.fetch(`/api/stats/${kind}?${params}`, {signal: controller.signal});
      if (!response.ok) throw new Error('Statistiques indisponibles. Réessayez.');
      const data = await response.json();
      if (current !== generation || controller.signal.aborted) return;
      const interval = `Du ${new Date(data.since).toLocaleString('fr-FR')} au ${new Date(data.until).toLocaleString('fr-FR')}`;
      status.textContent = interval;
      let node;
      if (kind === 'repetitions') node = table(['Titre', 'Radio', 'Passages', 'Intervalle moyen'], data.items.map(row => [trackLink(row), row.station_name, row.plays, row.average_seconds == null ? 'Insuffisant' : `${Math.round(row.average_seconds / 60)} min`]));
      if (kind === 'diversity') node = table(['Radio', 'Titres uniques', 'Passages'], data.items.map(row => [row.station_name, row.unique_tracks, row.plays]));
      if (kind === 'overlap') {
        const pairs = new Map(data.items.map(pair => [`${pair.station_a}:${pair.station_b}`, pair]));
        node = table(['Radio / titres uniques', ...data.stations.map(s => `${s.name} (${s.unique_tracks})`)], data.stations.map(a => [a.name, ...data.stations.map(b => {
          const pair = pairs.get(`${a.id}:${b.id}`);
          return pair.percent == null ? 'Non calculable' : `${pair.percent} % · ${pair.shared}/${pair.union}`;
        })]));
      }
      if (kind === 'genres') {
        node = bars(data.items, row => row.genre, row => row.plays);
        status.textContent += ` · Genres connus : ${data.coverage} % des ${data.total} passages`;
      }
      if (kind === 'offsets') node = table(['Titre', 'Première détection', 'Autre détection', 'Décalage'], data.items.map(row => {
        const first = row.seconds >= 0 ? Number(params.get('station_a')) : Number(params.get('station_b'));
        const second = row.seconds >= 0 ? Number(params.get('station_b')) : Number(params.get('station_a'));
        return [trackLink(row), `${names.get(first)} · ${new Date(row.seconds >= 0 ? row.at_a : row.at_b).toLocaleTimeString('fr-FR')}`, `${names.get(second)} · ${new Date(row.seconds >= 0 ? row.at_b : row.at_a).toLocaleTimeString('fr-FR')}`, row.seconds === 0 ? 'Même détection' : `${Math.abs(row.seconds)} s`];
      }));
      output.replaceChildren(node);
      previous.disabled = (pages[kind] || 1) <= 1; next.disabled = !data.has_more;
      section.querySelector('[data-page]').textContent = `Page ${pages[kind] || 1}`;
      section.querySelector('.pagination').hidden = ['genres', 'overlap'].includes(kind);
    } catch (error) {
      if (error.name !== 'AbortError' && current === generation) status.textContent = error.message;
    }
  }
  function refresh() {
    generation++;
    for (const section of root.querySelectorAll('[data-metric]')) { pages[section.dataset.metric] = 1; load(section.dataset.metric); }
  }
  for (const section of root.querySelectorAll('[data-metric]')) {
    const kind = section.dataset.metric;
    section.querySelector('[data-previous]').onclick = () => { pages[kind]--; load(kind); };
    section.querySelector('[data-next]').onclick = () => { pages[kind] = (pages[kind] || 1) + 1; load(kind); };
  }
  form.onsubmit = event => { event.preventDefault(); refresh(); };
  form.onchange = refresh; period.onchange = refresh;
  const onFilters = () => refresh(); window.addEventListener('radio:filterschange', onFilters); window.addEventListener('radio:catalogchange', onFilters);
  root.querySelector('[data-refresh]').onclick = refresh;
  refresh();
  return () => { controller.abort(); generation++; window.removeEventListener('radio:filterschange', onFilters); window.removeEventListener('radio:catalogchange', onFilters); };
};
