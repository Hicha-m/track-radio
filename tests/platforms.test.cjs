const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('app/static/platforms.js', 'utf8');
function events(object = {}) {
  const listeners = new Map();
  object.addEventListener = (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); };
  object.removeEventListener = (type, fn) => listeners.get(type)?.delete(fn);
  object.dispatch = type => { for (const fn of [...(listeners.get(type) || [])]) fn(); };
  object.listenerCount = () => [...listeners.values()].reduce((sum, set) => sum + set.size, 0);
  return object;
}
function setup() {
  let now = 0; let sequence = 0;
  const timers = new Map(); const navigations = [];
  const window = events({location: {href: 'https://radio.test/'}});
  window.location.assign = url => { navigations.push(url); };
  const document = events({visibilityState: 'visible'});
  runInNewContext(source, {window, document, URL,
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, {at: now + delay, fn}); return id; },
    clearTimeout(id) { timers.delete(id); }});
  function tick(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } }
  return {platforms: window.TrackPlatforms, window, document, navigations, tick, timers};
}
const links = setup().platforms.links;
test('one Spotify link contains native search and web fallback destinations', () => {
  const targets = links({provider: 'deezer', url: 'https://www.deezer.com/track/1'}, {artist: 'DEMI PORTION', title: 'On n’oublie pas'});
  assert.equal(targets.length, 2);
  assert.equal(targets[1].appUrl, 'spotify:search:DEMI%20PORTION%20On%20n%E2%80%99oublie%20pas');
  assert.equal(targets[1].url, 'https://open.spotify.com/search/DEMI%20PORTION%20On%20n%E2%80%99oublie%20pas');
});
test('a validated Spotify track opens the exact recording in either destination', () => {
  const targets = links({provider: 'spotify', provider_id: '7DstNl9Mumkt3ujs7V84qq'}, {title: 'Uber'});
  assert.equal(targets.length, 1);
  assert.equal(targets[0].appUrl, 'spotify:track:7DstNl9Mumkt3ujs7V84qq');
  assert.equal(targets[0].url, 'https://open.spotify.com/track/7DstNl9Mumkt3ujs7V84qq');
});
test('secondary platform links remain grouped and unsafe or duplicate URLs are skipped', () => {
  const targets = links({provider: 'deezer', url: 'https://www.deezer.com/track/1', additional_sources: [
    {provider: 'itunes', url: 'https://music.apple.com/track/42'},
    {provider: 'itunes', url: 'https://music.apple.com/track/43'},
    {provider: 'bad', url: 'javascript:alert(1)'},
  ]}, {title: 'Song'});
  assert.equal(targets.length, 3);
  assert.equal(targets[1].label, 'Ouvrir sur Apple Music');
});
test('tries app immediately and falls back exactly after 1000ms', () => {
  const env = setup(); const target = env.platforms.links({}, {title: 'Song'})[0];
  env.platforms.openWithFallback(target);
  assert.deepEqual(env.navigations, [target.appUrl]);
  env.tick(999); assert.equal(env.navigations.length, 1);
  env.tick(1); assert.deepEqual(env.navigations, [target.appUrl, target.url]);
  assert.equal(env.document.listenerCount() + env.window.listenerCount(), 0);
});
test('no web fallback after app takes focus or visibility, even when returning', () => {
  for (const event of ['visibilitychange', 'blur', 'pagehide']) {
    const env = setup(); const target = env.platforms.links({}, {title: 'Song'})[0];
    env.platforms.openWithFallback(target);
    if (event === 'visibilitychange') { env.document.visibilityState = 'hidden'; env.document.dispatch(event); env.document.visibilityState = 'visible'; env.document.dispatch(event); }
    else env.window.dispatch(event);
    env.tick(2000);
    assert.deepEqual(env.navigations, [target.appUrl]);
    assert.equal(env.document.listenerCount() + env.window.listenerCount(), 0);
  }
});
test('new clicks and internal navigation cancel older redirects', () => {
  const env = setup(); const first = env.platforms.links({}, {title: 'First'})[0];
  const second = env.platforms.links({}, {title: 'Second'})[0];
  env.platforms.openWithFallback(first); env.tick(500); env.platforms.openWithFallback(second);
  env.tick(500); assert.equal(env.navigations.length, 2);
  env.tick(500); assert.deepEqual(env.navigations, [first.appUrl, second.appUrl, second.url]);
  env.platforms.openWithFallback(first); env.window.dispatch('radio:pagechange'); env.tick(1000);
  assert.equal(env.navigations.length, 4);
  assert.equal(env.timers.size, 0);
});
