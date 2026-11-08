// Technical evidence only. Musical labels and provider payloads never enter this view.
(() => {
  const el = (...args) => window.RadioMetrics.el(...args);
  const t = (key, args) => window.RadioI18n.t(`monitor.${key}`, args);
  const date = value => value ? new Date(value).toLocaleString(window.RadioI18n.locale) : t('unknown');
  const latency = value => Number.isFinite(value) ? `${value} ms` : t('unknown');
  const seconds = value => Number.isFinite(value) ? `${value} s` : t('unknown');
  function field(list, label, value) { list.append(el('dt', label), el('dd', value)); }
  window.RadioCollectionMonitor = {
    mount(root, request) {
      const panel = root.querySelector('#collection-monitor');
      const form = panel.querySelector('#collection-filters');
      const message = panel.querySelector('#collection-status');
      const stations = panel.querySelector('#collection-stations');
      const events = panel.querySelector('#collection-events');
      const older = panel.querySelector('[data-collection-older]');
      const latest = panel.querySelector('[data-collection-latest]');
      let disposed = false, loading = false, rerun = false, generation = 0;
      let before = null, next = null;
      const probing = new Set();
      function render(data) {
        panel.querySelector('#collection-thresholds').textContent = t('thresholds', {
          observation: data.thresholds.observation_seconds,
          unchanged: data.thresholds.unchanged_seconds,
          hours: data.thresholds.retention_hours,
          perStation: data.thresholds.station_event_limit,
          total: data.thresholds.global_event_limit
        });
        const expandedMetrics = new Set([...stations.querySelectorAll('details[open]')].map(node => node.dataset.metrics));
        stations.replaceChildren();
        for (const item of data.items) {
          const card = el('article', '', 'collection-station');
          card.dataset.stationId = String(item.station_id); card.dataset.health = item.health;
          card.append(el('h3', `${item.name} · ${item.source.toUpperCase()}`), el('p', t(`health.${item.health}`), 'collection-health'));
          const summary = el('dl', '', 'collection-facts');
          for (const key of ['last_attempt_at', 'last_observation_at']) field(summary, t(key), date(item[key]));
          field(summary, t('last_result'), item.outcome ? t(`result.${item.outcome}`) : t('unknown'));
          field(summary, 'HTTP', item.http_status == null ? t('unknown') : String(item.http_status));
          field(summary, t('latency'), latency(item.duration_ms));
          field(summary, t('consecutive_errors'), String(item.consecutive_errors || 0));
          card.append(summary);
          const detail = el('details', '', 'collection-station-details'); detail.dataset.metrics = String(item.station_id);
          detail.open = expandedMetrics.has(detail.dataset.metrics);
          detail.append(el('summary', t('details')));
          const list = el('dl', '', 'collection-facts');
          for (const key of ['last_success_at', 'last_change_at', 'next_attempt_at']) field(list, t(key), date(item[key]));
          field(list, t('observation_age'), seconds(item.observation_age_seconds));
          field(list, t('change_age'), seconds(item.change_age_seconds));
          field(list, t('attempts'), String(item.attempts || 0));
          field(list, t('successes'), String(item.successes || 0));
          field(list, t('errors'), String(item.errors || 0));
          for (const key of ['poll_interval_seconds', 'request_timeout_seconds', 'collector_concurrency', 'max_stations', 'last_seen_write_seconds']) if (item.runtime[key] != null) field(list, t(key), String(item.runtime[key]));
          if (item.worker_revision) field(list, t('worker_revision'), item.worker_revision);
          detail.append(list); card.append(detail);
          if (item.probe_available) {
            const button = el('button', t('run_probes'), 'text-button'); button.type = 'button';
            const cooling = item.probe_requested_at && (Date.parse(data.checked_at) - Date.parse(item.probe_requested_at)) < data.thresholds.probe_cooldown_seconds * 1000;
            button.disabled = probing.has(item.station_id) || Boolean(cooling);
            button.onclick = async () => {
              button.disabled = true; probing.add(item.station_id);
              try {
                await request('/api/admin/collection/probes', {station_id: item.station_id});
                if (disposed) return;
                message.textContent = t('probes_queued');
                await refresh();
              } catch (error) {
                if (!disposed && error.name !== 'AbortError') message.textContent = error.message;
              } finally {
                probing.delete(item.station_id);
                if (!disposed) button.disabled = false;
              }
            };
            card.append(button, el('p', t('probe_help'), 'hint'));
            if (cooling) card.append(el('p', t('probe_last', {at: date(item.probe_requested_at)}), 'hint'));
          }
          stations.append(card);
        }
        if (!data.items.length) stations.append(el('p', t('no_stations')));
        const expanded = new Set([...events.querySelectorAll('details[open]')].map(node => node.dataset.attempt));
        events.replaceChildren();
        for (const item of data.events) {
          const detail = el('details', '', 'collection-event');
          detail.dataset.attempt = item.attempt_id; detail.dataset.outcome = item.outcome;
          detail.open = expanded.has(item.attempt_id);
          const source = ['skyrock_super', 'skyrock_onair'].includes(item.source) ? t(`source.${item.source}`) : item.source.toUpperCase();
          detail.append(el('summary', `${date(item.started_at)} · ${item.station_name} · ${t(item.kind)} / ${source} · ${t(`result.${item.outcome}`)}${item.http_status ? ` · HTTP ${item.http_status}` : ''} · ${latency(item.duration_ms)}`));
          const list = el('dl', '', 'collection-facts');
          field(list, t('attempt_id'), item.attempt_id);
          field(list, t('round_id'), item.round_id || t('unknown'));
          field(list, t('finished_at'), date(item.finished_at));
          field(list, t('next_attempt_at'), date(item.next_attempt_at));
          for (const key of ['provider_at', 'source_started_at', 'latest_ended_at']) if (item.details[key]) field(list, t(key), date(item.details[key]));
          for (const key of ['schedule_entries', 'active_entries']) if (item.details[key] != null) field(list, t(key), String(item.details[key]));
          for (const [index, response] of (item.details.http || []).entries()) {
            field(list, t('response', {number: index + 1}), `HTTP ${response.status}`);
            for (const key of ['date', 'last-modified']) if (response[key]) field(list, key === 'date' ? 'HTTP Date' : 'Last-Modified', date(response[key]));
            if (response.age != null) field(list, 'Age', seconds(response.age));
            if (response.cache?.length) field(list, 'Cache-Control', response.cache.join(', '));
          }
          detail.append(list); events.append(detail);
        }
        if (!data.events.length) events.append(el('p', t('no_events')));
        next = data.next_before_id; older.disabled = !next; latest.disabled = !before;
      }
      async function refresh() {
        if (disposed || document.hidden || !window.RadioAdmin.authenticated()) return;
        if (loading) { rerun = true; return; }
        loading = true;
        const ticket = generation;
        panel.setAttribute('aria-busy', 'true');
        try {
          const params = window.MusicFilters.apply(new URLSearchParams(new FormData(form)));
          if (!params.get('station_id')) params.delete('station_id');
          if (before) params.set('before_id', before);
          const data = await request(`/api/admin/collection?${params}`);
          if (disposed || generation !== ticket) return;
          render(data); message.textContent = t('checked', {at: date(data.checked_at)});
        } catch (error) {
          if (!disposed && generation === ticket && error.name !== 'AbortError') message.textContent = t('refresh_failed');
        } finally {
          loading = false; panel.setAttribute('aria-busy', 'false');
          if (rerun && !disposed) { rerun = false; refresh(); }
        }
      }
      function reset(event) {
        event?.preventDefault(); before = null; generation++; refresh();
      }
      form.addEventListener('change', reset); form.addEventListener('submit', reset);
      older.onclick = () => { before = next; generation++; refresh(); };
      latest.onclick = reset;
      window.addEventListener('radio:filterschange', reset);
      window.addEventListener('radio:globalfilterschange', reset);
      return {refresh, destroy() {
        disposed = true; generation++;
        form.removeEventListener('change', reset); form.removeEventListener('submit', reset);
        window.removeEventListener('radio:filterschange', reset);
        window.removeEventListener('radio:globalfilterschange', reset);
      }};
    }
  };
})();
