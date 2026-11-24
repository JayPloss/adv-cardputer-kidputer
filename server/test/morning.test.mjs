// Morning Overview v2 briefs: pure checks in-process, then the hub upload and device fetch routes
// against a scratch server (docs/morning-overview.md, "Data flow" and "Brief JSON").
import assert from 'node:assert/strict';
import { createHash, randomBytes, scryptSync } from 'node:crypto';
import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { addDays, easternDate, isMp3, validateBrief } from '../src/morning.mjs';

const sha = (value) => createHash('sha256').update(value).digest('hex');
const albieToken = randomBytes(24).toString('hex');
const jujuToken = randomBytes(24).toString('hex');
const papaDeviceToken = randomBytes(24).toString('hex');
const hubToken = randomBytes(32).toString('base64url');
const salt = randomBytes(16);
const passwordHash = `scrypt$${salt.toString('hex')}$${scryptSync('pw', salt, 32).toString('hex')}`;
const dataDir = mkdtempSync(join(tmpdir(), 'supachat-morning-'));
const port = 20196;
const base = `http://127.0.0.1:${port}`;
let child;

const today = easternDate();
const tomorrow = addDays(today, 1);
const brief = (date, extra = {}) => ({
  date,
  fr: { weather: 'Il fait -8, il vente, il neige.', speech: 'Bonjour Albert! Il fait moins huit.', audio_s: 52 },
  en: { weather: "It's -8, windy and snowing.", speech: 'Good morning Albert! It is minus eight.', audio_s: 50 },
  events: [{ time: '08:15', fr: 'Natation', en: 'Swimming' }, { time: '', fr: 'Pyjama', en: 'Pyjama day' }],
  ...extra,
});
// A CBR MPEG-2 Layer III frame header (22.05 kHz, 40 kb/s) followed by padding bytes.
const mp3 = (size, fill = 0) => Buffer.concat([Buffer.from([0xff, 0xf3, 0x58, 0xc4]), Buffer.alloc(size - 4, fill)]);

const put = (path, token, payload, type = 'application/json') => fetch(`${base}${path}`, {
  method: 'PUT',
  headers: { authorization: `Bearer ${token}`, 'content-type': type },
  body: Buffer.isBuffer(payload) ? payload : JSON.stringify(payload),
});
const get = (path, token) => fetch(`${base}${path}`, { headers: token ? { authorization: `Bearer ${token}` } : {} });

test('brief validation keeps known fields and refuses malformed ones', () => {
  const good = validateBrief({ ...brief('2026-11-25'), extra: 'dropped' }, '2026-11-25');
  assert.deepEqual(Object.keys(good), ['date', 'fr', 'en', 'events']);
  assert.equal(validateBrief(brief('2026-11-25'), '2026-11-26'), null, 'date must match the path');
  assert.equal(validateBrief(brief('2026-02-30'), '2026-02-30'), null, 'no impossible dates');
  assert.equal(validateBrief(brief('2026-11-25', { events: [{ time: '8:15', fr: 'a', en: 'b' }] }), '2026-11-25'), null);
  assert.equal(validateBrief(brief('2026-11-25', { events: [{ time: '24:00', fr: 'a', en: 'b' }] }), '2026-11-25'), null);
  assert.equal(validateBrief(brief('2026-11-25', { events: [{ time: '08:00', fr: 'a' }] }), '2026-11-25'), null);
  assert.equal(validateBrief(brief('2026-11-25', { en: { weather: 'x', speech: 'y', audio_s: '50' } }), '2026-11-25'), null);
  assert.equal(validateBrief(brief('2026-11-25', { fr: undefined }), '2026-11-25'), null);
  assert.equal(validateBrief(brief('2026-11-25', { events: undefined }), '2026-11-25'), null);
});

test('mp3 check accepts a frame sync or ID3, nothing else', () => {
  assert.ok(isMp3(mp3(100)));
  assert.ok(isMp3(Buffer.from('ID3\x04\x00\x00')));
  assert.ok(!isMp3(Buffer.from('RIFF....WAVE')));
  assert.ok(!isMp3(Buffer.from([0xff])));
});

test('dates: Eastern today and calendar arithmetic', () => {
  assert.equal(addDays('2026-11-30', 1), '2026-12-01');
  assert.equal(addDays('2026-03-08', 1), '2026-03-09');
  assert.equal(easternDate(Date.parse('2026-11-25T04:30:00Z')), '2026-11-24', '23:30 Eastern is still the 24th');
  assert.equal(easternDate(Date.parse('2026-11-25T05:30:00Z')), '2026-11-25');
});

test.before(async () => {
  // An old brief on disk, to be pruned by the next upload.
  const oldDate = addDays(today, -8);
  mkdirSync(join(dataDir, 'morning', 'albie'), { recursive: true });
  writeFileSync(join(dataDir, 'morning', 'albie', `${oldDate}.json`), '{}');
  child = spawn(process.execPath, ['--experimental-sqlite', 'src/server.mjs'], {
    cwd: join(import.meta.dirname, '..'),
    env: {
      PATH: process.env.PATH,
      SUPACHAT_HOST: '127.0.0.1', SUPACHAT_PORT: String(port), SUPACHAT_DATA_DIR: dataDir,
      SUPACHAT_SESSION_SECRET: randomBytes(32).toString('hex'), SUPACHAT_PAPA_PASSWORD_HASH: passwordHash,
      SUPACHAT_ALBIE_DEVICE_TOKEN_HASH: sha(albieToken), SUPACHAT_JUJU_DEVICE_TOKEN_HASH: sha(jujuToken),
      SUPACHAT_PAPA_DEVICE_TOKEN_HASH: sha(papaDeviceToken), SUPACHAT_PAPA_HUB_TOKEN_HASH: sha(hubToken),
      SUPACHAT_PORTAL_HOST: 'supachat.net',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('server start timeout')), 5000);
    child.stdout.on('data', (d) => { if (String(d).includes('listening')) { clearTimeout(timer); resolve(); } });
    child.stderr.on('data', (d) => process.stderr.write(d));
    child.on('exit', (code) => reject(new Error(`server exited ${code}`)));
  });
});

test.after(async () => {
  if (child && child.exitCode === null) { const exited = new Promise((r) => child.once('exit', r)); child.kill(); await exited; }
  rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
});

test('only an admin uploads, and only for an existing device user', async () => {
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}`, albieToken, brief(tomorrow))).status, 403, 'a kid cannot upload');
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}`, randomBytes(16).toString('hex'), brief(tomorrow))).status, 401);
  assert.equal((await put(`/api/hub/morning/papa/${tomorrow}`, hubToken, brief(tomorrow))).status, 404, 'papa is not a device user');
  assert.equal((await put(`/api/hub/morning/nobody/${tomorrow}`, hubToken, brief(tomorrow))).status, 404);
  assert.equal((await put(`/api/hub/morning/albie/${addDays(today, 9)}`, hubToken, brief(addDays(today, 9)))).status, 400, 'too far ahead');
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}`, hubToken, brief(today))).status, 400, 'date mismatch');
});

test('size and format limits', async () => {
  const big = brief(tomorrow, { fr: { weather: 'x', speech: 'é'.repeat(2100), audio_s: 50 } });
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}`, hubToken, big)).status, 413, 'over 4096 bytes in UTF-8');
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}/fr.mp3`, hubToken, Buffer.from('RIFF0000WAVEfmt '), 'audio/mpeg')).status, 400);
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}/fr.mp3`, hubToken, mp3(600 * 1024 + 1), 'audio/mpeg')).status, 413);
  assert.equal((await put(`/api/hub/morning/albie/${tomorrow}/de.mp3`, hubToken, mp3(100), 'audio/mpeg')).status, 404, 'unknown language is not a route');
});

test('upload, list, fetch, version tracks every part, old briefs pruned', async () => {
  const frAudio = mp3(40_000, 1);
  const enAudio = mp3(38_000, 2);
  const first = await put(`/api/hub/morning/albie/${tomorrow}`, hubToken, brief(tomorrow));
  assert.equal(first.status, 200);
  const v1 = (await first.json()).version;
  assert.match(v1, /^[0-9a-f]{8}$/);
  assert.ok(!existsSync(join(dataDir, 'morning', 'albie', `${addDays(today, -8)}.json`)), 'brief older than 7 days pruned');

  // Not listed until both audio files are in place.
  assert.deepEqual(await (await get('/api/device/morning', albieToken)).json(), { briefs: [] });
  assert.equal((await get(`/api/device/morning/${tomorrow}`, albieToken)).status, 404);
  const v2 = (await (await put(`/api/hub/morning/albie/${tomorrow}/fr.mp3`, hubToken, frAudio, 'audio/mpeg')).json()).version;
  const after = await put(`/api/hub/morning/albie/${tomorrow}/en.mp3`, hubToken, enAudio, 'audio/mpeg');
  const v3 = (await after.json()).version;
  assert.notEqual(v1, v2); assert.notEqual(v2, v3);

  const list = await (await get('/api/device/morning', albieToken)).json();
  assert.deepEqual(list, { briefs: [{ date: tomorrow, version: v3, fr_bytes: frAudio.length, en_bytes: enAudio.length }] });

  const one = await get(`/api/device/morning/${tomorrow}`, albieToken);
  assert.equal(one.status, 200);
  const stored = JSON.parse(await one.text());
  assert.deepEqual(Object.keys(stored), ['date', 'version', 'fr', 'en', 'events']);
  assert.equal(stored.version, v3);
  assert.deepEqual(stored.events, brief(tomorrow).events);

  for (const [lang, audio] of [['fr', frAudio], ['en', enAudio]]) {
    const response = await get(`/api/device/morning/${tomorrow}/${lang}.mp3`, albieToken);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-type'), 'audio/mpeg');
    assert.equal(Number(response.headers.get('content-length')), audio.length);
    assert.ok(Buffer.from(await response.arrayBuffer()).equals(audio));
  }

  // Same content again: same version. Changed text: new version.
  assert.equal((await (await put(`/api/hub/morning/albie/${tomorrow}`, hubToken, brief(tomorrow))).json()).version, v3);
  const changed = brief(tomorrow, { events: [] });
  assert.notEqual((await (await put(`/api/hub/morning/albie/${tomorrow}`, hubToken, changed)).json()).version, v3);

  // Papa's device token is an admin too; today's brief is listed beside tomorrow's.
  for (const part of [['', brief(today)], ['/fr.mp3', frAudio], ['/en.mp3', enAudio]]) {
    assert.equal((await put(`/api/hub/morning/albie/${today}${part[0]}`, papaDeviceToken, part[1], part[0] ? 'audio/mpeg' : 'application/json')).status, 200);
  }
  const dates = (await (await get('/api/device/morning', albieToken)).json()).briefs.map((b) => b.date);
  assert.deepEqual(dates, [today, tomorrow]);
});

test('a device sees only its own briefs; no token, no briefs', async () => {
  assert.deepEqual(await (await get('/api/device/morning', jujuToken)).json(), { briefs: [] });
  assert.equal((await get(`/api/device/morning/${tomorrow}`, jujuToken)).status, 404);
  assert.equal((await get(`/api/device/morning/${tomorrow}/fr.mp3`, jujuToken)).status, 404);
  assert.equal((await get('/api/device/morning', null)).status, 401);
  assert.deepEqual(await (await get('/api/device/morning', hubToken)).json(), { briefs: [] }, 'the hub key is Papa, who has no device briefs');
  assert.equal((await fetch(`${base}/api/device/morning`, { method: 'POST', headers: { authorization: `Bearer ${albieToken}` } })).status, 405);
  assert.ok(readdirSync(join(dataDir, 'morning')).every((user) => user === 'albie'));
});
