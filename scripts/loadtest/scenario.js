// HTTP model of visible pages. No audio, external assets, writes or admin token.
import http from 'k6/http';
import { sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const config = JSON.parse(open(__ENV.PROFILE));
const base = config.base_url.replace(/\/$/, '');
const target = Number(__ENV.TARGET_VUS || 1);
if (!Number.isInteger(target) || target < 1 || target > config.max_vus || target > 500) {
  throw new Error('Invalid VU bound');
}
const apiLatency = new Trend('api_latency', true);
const apiErrors = new Rate('api_errors');
const routeMetrics = {};
for (const route of ['session', 'exclusions', 'now', 'top', 'explore', 'catalog', 'history', 'artwork']) {
  routeMetrics[route] = {time: new Trend(`api_${route}_ms`, true), errors: new Rate(`api_${route}_errors`), count: new Counter(`api_${route}_count`)};
}
const siteErrors = new Rate('site_errors');
const sessions = new Counter('sessions_started');
const missingArtwork = new Counter('missing_station_artwork');
const skippedExternal = new Counter('external_assets_skipped');
const stableStart = config.ramp_seconds + config.warmup_seconds;
export const options = {
  scenarios: {users: {executor: 'ramping-vus', startVUs: 0,
    stages: [{duration: `${config.ramp_seconds}s`, target},
      {duration: `${config.warmup_seconds + config.steady_seconds}s`, target},
      {duration: '5s', target: 0}], gracefulRampDown: '0s', gracefulStop: '0s'}},
  thresholds: {api_errors: [{threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '60s'}],
    site_errors: [{threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '30s'}]},
  discardResponseBodies: true,
  systemTags: ['name', 'method', 'status', 'scenario', 'expected_response'],
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
  userAgent: 'RadioTrackerCapacity/1.0',
};
let randomState = 1;
let observedTracks = [];
let stationIds = [];
let assets = new Set();
let selectedStation = null;
let nextCards = Infinity, nextPlayer = Infinity, nextChart = Infinity;
let page = '/', days = 7;
function rand() {
  randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0;
  return randomState / 4294967296;
}
function pick(items) { return items.length ? items[Math.floor(rand() * items.length)] : null; }
function query(params) {
  return Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
}
function stable() {
  const seconds = (Date.now() - exec.scenario.startTime) / 1000;
  return seconds >= stableStart && seconds < stableStart + config.steady_seconds;
}
function request(path, name, validate = null, allowRedirect = false) {
  const measure = stable();
  const response = http.get(base + path, {redirects: 0, timeout: `${config.request_timeout_seconds}s`,
    responseType: validate || name === 'html' ? 'text' : 'none', tags: {name}});
  let data = null;
  const missingLogo = name === 'artwork' && response.status === 404;
  if (missingLogo) missingArtwork.add(1);
  let ok = missingLogo || response.status === 200 || (allowRedirect && [301, 302, 303, 307, 308].includes(response.status));
  if (ok && validate) {
    try { data = response.json(); ok = validate(data); } catch (_) { ok = false; }
  }
  if (name === 'html' || name === 'asset') siteErrors.add(!ok);
  if (routeMetrics[name] && measure) {
    apiLatency.add(response.timings.duration); apiErrors.add(!ok);
    routeMetrics[name].time.add(response.timings.duration);
    routeMetrics[name].errors.add(!ok); routeMetrics[name].count.add(1);
  }
  return {response, data, ok};
}
function revision() { request('/api/exclusions/revision', 'exclusions', d => typeof d.revision === 'string'); }
function session() { request('/api/admin/session', 'session', d => typeof d === 'object' && d !== null); }
function image(url) {
  if (!url || assets.has(url)) return;
  assets.add(url);
  const path = url.startsWith(base + '/') ? url.slice(base.length) : url;
  const name = /^\/api\/stations\/\d+\/artwork(?:[?]|$)/.test(path) ? 'artwork' : 'asset';
  if (path.startsWith('/')) request(path, name, null, name === 'artwork');
  else skippedExternal.add(1);
}
function remember(items) {
  observedTracks = items.filter(x => x && typeof x.title === 'string');
  observedTracks.forEach(x => image(x.metadata?.cover_url));
}
function nowPlaying(station = null) {
  const {data} = request('/api/stations/now-playing' + (station ? `?station_id=${station}` : ''), 'now',
    d => Array.isArray(d.items) && typeof d.checked_at === 'string');
  if (data?.items) {
    if (!station) stationIds = data.items.map(x => x.station_id).filter(Number.isInteger);
    data.items.forEach(x => image(x.track?.metadata?.cover_url));
    if (!observedTracks.length) observedTracks = data.items.map(x => x.track).filter(x => x?.title);
  }
}
function chart() {
  const since = new Date(Math.floor(Date.now() / 30000) * 30000 - days * 86400000).toISOString();
  const {data} = request('/api/stats/top?' + query({limit: 20, since}), 'top', Array.isArray);
  if (Array.isArray(data)) remember(data);
}
function shell(path, initial = false) {
  const {response} = request(path, 'html');
  if (initial && response.status === 200) {
    // The browser downloads same-origin static assets once per new session.
    for (const match of response.body.matchAll(/(?:src|href)=["'](\/static\/[^"']+)["']/g)) image(match[1]);
    for (const match of response.body.matchAll(/src=["'](\/api\/stations\/\d+\/artwork)["']/g)) image(match[1]);
  }
  if (initial) session();
  revision();
  page = path.split('?')[0];
  nowPlaying(); nextCards = Date.now() + 30000;
  if (page === '/') { chart(); nextChart = Date.now() + 30000; }
  else nextChart = Infinity;
}
function dwell(seconds) {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    const at = Date.now();
    if (at >= nextCards) { nextCards = at + 30000; nowPlaying(); }
    if (at >= nextPlayer) { nextPlayer = at + 20000; nowPlaying(selectedStation); }
    if (at >= nextChart) { nextChart = at + 30000; chart(); }
    sleep(Math.min(1, Math.max(0, (until - Date.now()) / 1000)));
  }
}
function think() { dwell(config.think_seconds[0] + rand() * (config.think_seconds[1] - config.think_seconds[0])); }
function details() {
  const track = pick(observedTracks);
  if (!track) return;
  session();
  const {data} = request('/api/catalog?' + query({artist: track.artist || '', title: track.title}), 'catalog',
    d => typeof d.track_id === 'string');
  if (data?.track_id) request(`/api/catalog/${data.track_id}/radio`, 'history', d => typeof d.detections === 'number');
}
export default function () {
  randomState = (config.seed + Math.imul(exec.vu.idInTest, 2654435761) + exec.vu.iterationInInstance) >>> 0;
  assets = new Set(); observedTracks = []; stationIds = []; selectedStation = null;
  nextCards = nextPlayer = nextChart = Infinity; days = 7;
  const began = Date.now();
  // Desynchronise visits, unlike a simultaneous artificial refresh storm.
  sleep(rand() * config.think_seconds[1]);
  sessions.add(1);
  const roll = rand() * 100;
  shell('/', true); think();
  if (roll < config.weights.radio) {
    shell('/radios'); selectedStation = pick(stationIds);
    if (selectedStation) { nowPlaying(selectedStation); nextPlayer = Date.now() + 20000; }
    think(); details();
  } else if (roll < config.weights.radio + config.weights.ranking) {
    days = pick(config.chart_days); chart(); think(); details();
  } else {
    shell('/recherche');
    const first = request('/api/explore?view=plays&sort=newest&page=1&page_size=50', 'explore', d => Array.isArray(d.items));
    if (first.data) remember(first.data.items);
    think();
    const track = pick(observedTracks);
    if (track) {
      const result = request('/api/explore?' + query({view: 'tracks', sort: 'frequency', q: track.artist || track.title, page_size: 50}),
        'explore', d => Array.isArray(d.items));
      if (result.data) remember(result.data.items);
    }
    think(); details();
  }
  dwell(Math.max(0, config.session_seconds - (Date.now() - began) / 1000));
}
export function handleSummary(data) {
  // No URLs, response bodies, cookies, environment, machine names or user data.
  const metrics = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (/^(api_|http_reqs$|http_req_failed$|iterations$|vus$|vus_max$|data_received$|data_sent$|sessions_started$|missing_station_artwork$|site_errors$|external_assets_skipped$)/.test(name)) {
      metrics[name] = metric.values;
    }
  }
  return {[__ENV.RESULT_FILE]: JSON.stringify({metrics}, null, 2)};
}
