const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('app/static/cache.js', 'utf8');
function create(storage = {}) {
  let time = 1000;
  const sessionStorage = new Proxy(storage, {get(target, key) {
    if (key === 'length') return Object.keys(target).length;
    if (key === 'key') return i => Object.keys(target)[i];
    if (key === 'getItem') return k => target[k];
    if (key === 'setItem') return (k, v) => { target[k] = v; };
    if (key === 'removeItem') return k => { delete target[k]; };
    return target[key];
  }});
  const context = {window: {}, document: {body: {dataset: {assetVersion: 'test'}}}, sessionStorage, Date: {now: () => time}};
  runInNewContext(source, context);
  return {cache: context.window.RadioCache, tick: n => {time += n;}, storage};
}
test('filters remain isolated and expire', () => {
  const {cache, tick} = create();
  cache.put('chart:1:7', ['one']);
  cache.put('chart:2:7', ['two']);
  assert.equal(cache.get('chart:1:7').data[0], 'one');
  assert.equal(cache.get('chart:2:7').data[0], 'two');
  tick(300001);
  assert.equal(cache.get('chart:1:7'), null);
});
test('persists across navigation and evicts least recently used entries', () => {
  const {cache, storage} = create();
  for (let i = 0; i < 40; i++) cache.put(String(i), i);
  cache.get('0');
  cache.put('40', 40);
  assert.equal(cache.get('1'), null);
  assert.equal(Object.keys(storage).length, 40);
  assert.equal(create(storage).cache.get('0').data, 0);
});
test('unavailable storage does not break filtering', () => {
  const storage = new Proxy({}, {get() {throw new Error('disabled');}, ownKeys() {throw new Error('disabled');}});
  const {cache} = create(storage);
  cache.put('search:a', {items: []});
  assert.ok(cache.get('search:a'));
});
