// Morning Overview v2: briefs (text fr/en + one MP3 per language) per device user and date.
// Contract: docs/morning-overview.md ("Data flow", "Brief JSON"). The generator on paqman
// (tools/morning/morning-brief.py) uploads with an admin key; each device fetches only its own.
// server.mjs calls initMorning() once, then handleMorning() after a request is authenticated.
// Files: <dataDir>/morning/<user>/<date>.json, <date>.fr.mp3, <date>.en.mp3.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const MORNING_TZ = 'America/Toronto';
export const MAX_BRIEF_BYTES = 4096;
export const MAX_AUDIO_BYTES = 600 * 1024;
export const KEEP_DAYS = 7;
const LANGS = ['fr', 'en'];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

let db;
let rootDir;

export function initMorning(database, dataDir) {
  db = database;
  rootDir = join(dataDir, 'morning');
  mkdirSync(rootDir, { recursive: true });
}

// YYYY-MM-DD for an instant in Eastern time.
export function easternDate(ms = Date.now()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: MORNING_TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(ms);
}

// Calendar arithmetic on a YYYY-MM-DD string (UTC noon avoids any DST edge).
export function addDays(date, days) {
  const at = new Date(`${date}T12:00:00Z`);
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

export function validDate(date) {
  if (!DATE_RE.test(date)) return false;
  return new Date(`${date}T12:00:00Z`).toISOString().slice(0, 10) === date;
}

// Returns the normalized brief (only known fields, in contract order, without version) or null.
export function validateBrief(input, date) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null;
  if (input.date !== date || !validDate(date)) return null;
  const brief = { date };
  for (const lang of LANGS) {
    const part = input[lang];
    if (!part || typeof part !== 'object' || Array.isArray(part)) return null;
    if (typeof part.weather !== 'string' || typeof part.speech !== 'string') return null;
    if (typeof part.audio_s !== 'number' || !Number.isFinite(part.audio_s) || part.audio_s < 0 || part.audio_s > 600) return null;
    brief[lang] = { weather: part.weather, speech: part.speech, audio_s: part.audio_s };
  }
  if (!Array.isArray(input.events)) return null;
  brief.events = [];
  for (const event of input.events) {
    if (!event || typeof event !== 'object' || Array.isArray(event)) return null;
    const time = event.time ?? '';
    if (typeof time !== 'string' || !(time === '' || /^([01]\d|2[0-3]):[0-5]\d$/.test(time))) return null;
    if (typeof event.fr !== 'string' || typeof event.en !== 'string') return null;
    brief.events.push({ time, fr: event.fr, en: event.en });
  }
  return brief;
}

export function isMp3(buffer) {
  if (buffer.length < 4) return false;
  if (buffer.subarray(0, 3).toString('latin1') === 'ID3') return true;
  return buffer[0] === 0xff && (buffer[1] & 0xe0) === 0xe0;
}

const userDir = (user) => join(rootDir, user);
const paths = (user, date) => ({
  json: join(userDir(user), `${date}.json`),
  fr: join(userDir(user), `${date}.fr.mp3`),
  en: join(userDir(user), `${date}.en.mp3`),
});

function writeAtomic(file, content) {
  const temp = `${file}.tmp-${process.pid}`;
  writeFileSync(temp, content, { mode: 0o600 });
  renameSync(temp, file);
}

function readBrief(user, date) {
  try { return JSON.parse(readFileSync(paths(user, date).json, 'utf8')); } catch { return null; }
}

const fileBytes = (file) => { try { return statSync(file).size; } catch { return 0; } };

// The version covers the brief text and both audio files, so any re-upload changes it.
function restamp(user, date) {
  const p = paths(user, date);
  const brief = readBrief(user, date);
  if (!brief) return null;
  const { version: _old, ...rest } = brief;
  const content = JSON.stringify(rest);
  const hash = createHash('sha256').update(content);
  for (const lang of LANGS) if (existsSync(p[lang])) hash.update(readFileSync(p[lang]));
  const stored = { date: rest.date, version: hash.digest('hex').slice(0, 8), fr: rest.fr, en: rest.en, events: rest.events };
  writeAtomic(p.json, JSON.stringify(stored));
  return stored.version;
}

// Drops every user's briefs dated more than KEEP_DAYS before today (Eastern).
function prune() {
  const oldest = addDays(easternDate(), -KEEP_DAYS);
  for (const user of readdirSync(rootDir)) {
    let files;
    try { files = readdirSync(userDir(user)); } catch { continue; }
    for (const file of files) {
      const date = file.slice(0, 10);
      if (DATE_RE.test(date) && date < oldest) rmSync(join(userDir(user), file), { force: true });
    }
  }
}

function deviceUserExists(user) {
  const row = db.prepare('SELECT kind, revoked_at FROM users WHERE id = ?').get(user);
  return Boolean(row && row.kind === 'device' && !row.revoked_at);
}

// A brief is offered to the device only once its text and both audio files are in place.
function complete(user, date) {
  const p = paths(user, date);
  return existsSync(p.json) && existsSync(p.fr) && existsSync(p.en);
}

export async function handleMorning(req, res, url, user, ctx) {
  const { json } = ctx;
  const send = (status, value) => { json(res, status, value); return true; };

  const hub = url.pathname.match(/^\/api\/hub\/morning\/([a-z0-9_-]{1,40})\/(\d{4}-\d{2}-\d{2})(?:\/(fr|en)\.mp3)?$/);
  if (hub) {
    if (req.method !== 'PUT') return send(405, { error: 'method_not_allowed' });
    if (ctx.webUser(req) && !ctx.sameOrigin(req)) return send(403, { error: 'origin_rejected' });
    if (db.prepare('SELECT role FROM users WHERE id = ?').get(user)?.role !== 'admin') return send(403, { error: 'admin_only' });
    const [, target, date, lang] = hub;
    if (!deviceUserExists(target)) return send(404, { error: 'unknown_device_user' });
    if (!validDate(date)) return send(400, { error: 'invalid_date' });
    const today = easternDate();
    if (date < addDays(today, -KEEP_DAYS) || date > addDays(today, KEEP_DAYS)) return send(400, { error: 'date_out_of_range' });
    mkdirSync(userDir(target), { recursive: true });
    const p = paths(target, date);
    if (lang) {
      let audio;
      try { audio = await ctx.rawBody(req, MAX_AUDIO_BYTES); } catch { return send(413, { error: 'audio_too_large', max_bytes: MAX_AUDIO_BYTES }); }
      if (!isMp3(audio)) return send(400, { error: 'invalid_mp3' });
      writeAtomic(p[lang], audio);
    } else {
      let input;
      try { input = await ctx.body(req, MAX_BRIEF_BYTES * 2); } catch (error) {
        return send(error.message === 'body_too_large' ? 413 : 400, { error: error.message === 'body_too_large' ? 'brief_too_large' : 'invalid_json' });
      }
      const brief = validateBrief(input, date);
      if (!brief) return send(400, { error: 'invalid_brief' });
      // Measured with a version in place, as the device stores it.
      if (Buffer.byteLength(JSON.stringify({ ...brief, version: '00000000' })) > MAX_BRIEF_BYTES) {
        return send(413, { error: 'brief_too_large', max_bytes: MAX_BRIEF_BYTES });
      }
      writeAtomic(p.json, JSON.stringify(brief));
    }
    const version = restamp(target, date);
    prune();
    return send(200, { ok: true, user: target, date, version, complete: complete(target, date) });
  }

  if (url.pathname !== '/api/device/morning' && !url.pathname.startsWith('/api/device/morning/')) return false;
  if (req.method !== 'GET') return send(405, { error: 'method_not_allowed' });
  // Only a device's own bearer token; a web or app session has no briefs to fetch.
  const device = ctx.deviceUser(req);
  if (!device) return send(401, { error: 'device_token_required' });

  if (url.pathname === '/api/device/morning') {
    const today = easternDate();
    const briefs = [];
    for (const date of [today, addDays(today, 1)]) {
      if (!complete(device, date)) continue;
      const brief = readBrief(device, date);
      if (!brief) continue;
      const p = paths(device, date);
      briefs.push({ date, version: brief.version, fr_bytes: fileBytes(p.fr), en_bytes: fileBytes(p.en) });
    }
    return send(200, { briefs });
  }

  const one = url.pathname.match(/^\/api\/device\/morning\/(\d{4}-\d{2}-\d{2})(?:\/(fr|en)\.mp3)?$/);
  if (!one || !validDate(one[1]) || !complete(device, one[1])) return send(404, { error: 'brief_not_found' });
  const [, date, lang] = one;
  if (!lang) {
    const content = readFileSync(paths(device, date).json);
    res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'content-length': content.length, 'cache-control': 'no-store' });
    res.end(content);
    return true;
  }
  const audio = readFileSync(paths(device, date)[lang]);
  res.writeHead(200, { 'content-type': 'audio/mpeg', 'content-length': audio.length, 'cache-control': 'no-store' });
  res.end(audio);
  return true;
}
