const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('app/static/filters.js', 'utf8');

function create({authenticated = false, stored = [], request, fetch: fetchImpl} = {}) {
  const storage = {'radio-hidden-tracks-v1': JSON.stringify(stored)};
  const events = [];
  const listeners = {};
  let clears = 0;
  const window = {
    RadioAdmin: {authenticated: () => authenticated, request},
    RadioCache: {clear: () => clears++},
    addEventListener: (name, callback) => { listeners[name] = callback; },
    dispatchEvent: event => { events.push(event.type); listeners[event.type]?.(event); },
    setInterval: () => {},
  };
  runInNewContext(source, {
    window, document: {querySelectorAll: () => [], addEventListener: () => {}},
    localStorage: {getItem: key => storage[key], setItem: (key, value) => { storage[key] = value; }},
    URLSearchParams, DOMException, Event,
    fetch: fetchImpl || (async () => ({ok: true, json: async () => ({revision: 'initial'})})),
  });
  return {filters: window.MusicFilters, storage, events, clears: () => clears};
}
const track = {artist: 'Artist', title: 'Song'};

test('personal hiding is isolated between browsers and survives admin use', async () => {
  const one = create(); const two = create();
  await one.filters.hide(track);
  assert.equal(one.filters.apply(new URLSearchParams()).get('exclusions'), JSON.stringify([track]));
  assert.equal(two.filters.apply(new URLSearchParams()).get('exclusions'), null);
  const before = JSON.stringify([track]);
  const admin = create({authenticated: true, stored: [track], request: async () => ({revision: 'new'})});
  await admin.filters.hide({title: 'Global'});
  assert.equal(admin.storage['radio-hidden-tracks-v1'], before);
  assert.ok(admin.events.includes('radio:globalfilterschange'));
});

test('admin hiding waits for server and failures never become personal exclusions', async () => {
  let complete;
  const admin = create({authenticated: true, request: () => new Promise(resolve => { complete = resolve; })});
  let finished = false;
  const saving = admin.filters.hide(track).then(() => { finished = true; });
  assert.equal(finished, false);
  assert.equal(admin.events.length, 0);
  complete({revision: 'saved'}); await saving;
  assert.equal(finished, true);
  assert.equal(admin.storage['radio-hidden-tracks-v1'], '[]');
  const expired = create({request: async () => { throw new Error('Session expirée'); }});
  await assert.rejects(expired.filters.hide(track, true), /Session expirée/);
  assert.equal(expired.storage['radio-hidden-tracks-v1'], '[]');
  assert.equal(expired.events.length, 0);
});

test('global revision invalidates caches and rejects responses already in flight', async () => {
  let body;
  const browser = create({authenticated: true, request: async () => ({revision: 'updated'}), fetch: async url => ({
    ok: true, json: () => url.includes('/revision') ? Promise.resolve({revision: 'initial'}) : new Promise(resolve => { body = resolve; }),
  })});
  await browser.filters.refresh();
  const oldKey = browser.filters.key();
  const response = await browser.filters.fetch('/api/explore');
  const reading = response.json();
  await browser.filters.hide(track);
  body({items: [track]});
  await assert.rejects(reading, {name: 'AbortError'});
  assert.notEqual(browser.filters.key(), oldKey);
  assert.equal(browser.clears(), 2);
});

test('a revision check started before a mutation cannot restore the old revision', async () => {
  let finish;
  const browser = create({authenticated: true, request: async () => ({revision: 'new'}), fetch: async () => ({
    ok: true, json: () => new Promise(resolve => { finish = resolve; }),
  })});
  const checking = browser.filters.refresh();
  await Promise.resolve();
  await browser.filters.hide(track);
  const key = browser.filters.key();
  finish({revision: 'old'}); await checking;
  assert.equal(browser.filters.key(), key);
});
