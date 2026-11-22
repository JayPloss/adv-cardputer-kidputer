// Parent controls: pure functions in-process, then the HTTP routes against a scratch server.
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes, scryptSync } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { blockedFeatures, defaultUsername, hashParentCode, initParentControls, validateBlackouts, verifyParentCode } from '../src/parent-controls.mjs';

test('blackout windows: same-day, overnight, and the morning after', () => {
  const school = validateBlackouts([{ label: 'School nights', days: [0, 1, 2, 3, 4], start: '21:00', end: '07:00', features: ['music', 'chat'] }]);
  const lunch = validateBlackouts([{ days: [1], start: '12:00', end: '13:00', features: ['podcasts'] }]);
  assert.deepEqual(blockedFeatures(school, 0, 21 * 60), ['chat', 'music']);      // Sunday 21:00
  assert.deepEqual(blockedFeatures(school, 1, 6 * 60 + 59), ['chat', 'music']);  // Monday 06:59, from Sunday
  assert.deepEqual(blockedFeatures(school, 1, 7 * 60), []);                      // Monday 07:00
  assert.deepEqual(blockedFeatures(school, 6, 3 * 60), []);                      // Saturday 03:00: Friday not listed
  assert.deepEqual(blockedFeatures(school, 5, 3 * 60), ['chat', 'music']);       // Friday 03:00, from Thursday
  assert.deepEqual(blockedFeatures(lunch, 1, 12 * 60 + 30), ['podcasts']);
  assert.deepEqual(blockedFeatures(lunch, 2, 12 * 60 + 30), []);
});

test('blackout validation refuses unknown features, bad times and always-allowed functions', () => {
  assert.equal(validateBlackouts([{ days: [1], start: '21:00', end: '07:00', features: ['alarms'] }]), null);
  assert.equal(validateBlackouts([{ days: [1], start: '25:00', end: '07:00', features: ['music'] }]), null);
  assert.equal(validateBlackouts([{ days: [], start: '21:00', end: '07:00', features: ['music'] }]), null);
  assert.equal(validateBlackouts([{ days: [1], start: '21:00', end: '21:00', features: ['music'] }]), null);
  assert.equal(validateBlackouts('nope'), null);
});

test('parent code hash verifies and rejects', () => {
  const stored = hashParentCode('482913');
  assert.ok(verifyParentCode('482913', stored));
  assert.ok(!verifyParentCode('482914', stored));
});

test('default usernames are readable and unique', () => {
  const db = new DatabaseSync(':memory:');
  db.exec("CREATE TABLE users(id TEXT PRIMARY KEY, display_name TEXT, short_name TEXT, kind TEXT, role TEXT); CREATE TABLE user_groups(id TEXT PRIMARY KEY, name TEXT); CREATE TABLE user_group_members(group_id TEXT, user_id TEXT)");
  initParentControls(db);
  assert.equal(defaultUsername('Marie Dupont'), 'marie-d');
  assert.equal(defaultUsername('Élodie'), 'elodie');
  db.prepare("INSERT INTO users VALUES ('marie-d','Marie','Marie','web','member')").run();
  assert.equal(defaultUsername('Marie Dubois'), 'marie-d2');
  assert.equal(defaultUsername('!!'), 'friend');
});

const password = 'integration-password';
const albieToken = randomBytes(24).toString('hex');
const albieHash = createHash('sha256').update(albieToken).digest('hex');
const nativeTestToken = randomBytes(24).toString('hex');
const salt = randomBytes(16);
const passwordHash = `scrypt$${salt.toString('hex')}$${scryptSync(password, salt, 32).toString('hex')}`;
const dataDir = mkdtempSync(join(tmpdir(), 'supachat-pc-'));
const port = 18096;
const base = `http://127.0.0.1:${port}`;
let child; let papa; let mama; let marie;

// Signed in through the portal's Authentik proxy headers, as the existing server tests do.
const portal = (uid, username, name) => ({ 'x-forwarded-host': 'supachat.net', 'x-authentik-uid': uid, 'x-authentik-username': username, 'x-authentik-name': name, 'content-type': 'application/json' });
const as = (headers) => headers;
test.before(async () => {
  child = spawn(process.execPath, ['--experimental-sqlite', 'src/server.mjs'], {
    cwd: join(import.meta.dirname, '..'),
    env: { ...process.env, SUPACHAT_HOST: '127.0.0.1', SUPACHAT_PORT: String(port), SUPACHAT_DATA_DIR: dataDir,
      SUPACHAT_SESSION_SECRET: randomBytes(32).toString('hex'), SUPACHAT_PAPA_PASSWORD_HASH: passwordHash,
      SUPACHAT_ALBIE_DEVICE_TOKEN_HASH: albieHash, SUPACHAT_PORTAL_HOST: 'supachat.net', SUPACHAT_NATIVE_TEST_TOKEN: nativeTestToken },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('server start timeout')), 5000);
    child.stdout.on('data', (d) => { if (String(d).includes('listening')) { clearTimeout(timer); resolve(); } });
    child.stderr.on('data', (d) => process.stderr.write(d));
  });
});
test.after(() => { child?.kill(); rmSync(dataDir, { recursive: true, force: true }); });

test('portal identities for the route tests', () => {
  papa = portal('uid-papa', 'papa', 'Papa'); mama = portal('uid-mama', 'mama', 'Mama');
});

test('a parent with no kids sees none and cannot edit a device', async () => {
  const kids = await (await fetch(`${base}/api/manage/kids`, { headers: as(mama) })).json();
  assert.deepEqual(kids.kids, []);
  const r = await fetch(`${base}/api/manage/kids/albie/policy`, { method: 'PUT', headers: as(mama), body: JSON.stringify({ blackouts: [] }) });
  assert.equal(r.status, 403);
});

test('the admin makes Mama a parent of Albie; Mama edits the policy; the device gets it signed', async () => {
  assert.equal((await fetch(`${base}/api/manage/kids/albie/guardians/mama`, { method: 'PUT', headers: as(papa) })).status, 200);
  const blackouts = [{ label: 'School nights', days: [0, 1, 2, 3, 4], start: '21:00', end: '07:00', features: ['music', 'podcasts', 'chat'] }];
  const missingCode = await fetch(`${base}/api/manage/kids/albie/policy`, { method: 'PUT', headers: as(mama), body: JSON.stringify({ blackouts, clock_override_enabled: true }) });
  assert.equal((await missingCode.json()).error, 'parent_code_required');
  const saved = await fetch(`${base}/api/manage/kids/albie/policy`, { method: 'PUT', headers: as(mama), body: JSON.stringify({ blackouts, clock_override_enabled: true, device_settings_enabled: true, parent_code: '482913' }) });
  assert.equal(saved.status, 200);
  const policy = (await saved.json()).policy;
  assert.equal(policy.has_parent_code, true);
  assert.ok(!JSON.stringify(policy).includes('482913'));

  const device = await fetch(`${base}/api/device/policy`, { headers: { authorization: `Bearer ${albieToken}` } });
  const envelope = await device.json();
  assert.equal(envelope.sig, createHmac('sha256', albieHash).update(envelope.payload).digest('hex'));
  const payload = JSON.parse(envelope.payload);
  assert.equal(payload.user, 'albie');
  assert.deepEqual(payload.blackouts, [{ ...blackouts[0], features: ['chat', 'music', 'podcasts'] }]);
  assert.equal(payload.clock_override, true);
  assert.equal(payload.clock_trust.unknown, 'allow_media');
  assert.ok(payload.valid_until > payload.issued_at && Math.abs(payload.server_time - Date.now()) < 5000);
  assert.equal(payload.parent_code.kdf, 'pbkdf2-sha256');
});

test('management is refused from a device; parent mode on the device needs the code', async () => {
  assert.equal((await fetch(`${base}/api/manage/kids`, { headers: { authorization: `Bearer ${albieToken}` } })).status, 403);
  const blackouts = [{ days: [5, 6], start: '23:00', end: '08:00', features: ['music'] }];
  const wrong = await fetch(`${base}/api/device/policy`, { method: 'PUT', headers: { authorization: `Bearer ${albieToken}`, 'content-type': 'application/json' }, body: JSON.stringify({ code: '000000', blackouts }) });
  assert.equal(wrong.status, 403);
  const right = await fetch(`${base}/api/device/policy`, { method: 'PUT', headers: { authorization: `Bearer ${albieToken}`, 'content-type': 'application/json' }, body: JSON.stringify({ code: '482913', blackouts }) });
  assert.equal(right.status, 200);
  assert.deepEqual(JSON.parse((await right.json()).payload).blackouts, [{ label: '', ...blackouts[0] }]);
});

test('a kid shares from the device; a new person renames once and joins; the inviting kid\'s parent sees them', async () => {
  const shared = await fetch(`${base}/api/device/invites`, { method: 'POST', headers: { authorization: `Bearer ${albieToken}`, 'content-type': 'application/json' }, body: JSON.stringify({ name: 'Marie Dupont' }) });
  assert.equal(shared.status, 201);
  const invite = await shared.json();
  assert.equal(invite.username, 'marie-d');
  const token = invite.url.split('/').pop();
  assert.equal((await fetch(`${base}/join/${token}`)).status, 200);
  const info = await (await fetch(`${base}/api/join/${token}`)).json();
  assert.equal(info.username, 'marie-d'); assert.equal(info.can_change_username, true); assert.equal(info.kind, 'kid');
  const enroll = await fetch(`${base}/api/join/${token}/enroll`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'mariedup' }) });
  assert.equal(enroll.status, 201);
  const enrollment = await enroll.json();
  assert.match(enrollment.url, /itoken=/); assert.match(decodeURIComponent(enrollment.url), /\/join\/.+\?done=1/);
  const again = await fetch(`${base}/api/join/${token}/enroll`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'marie2' }) });
  assert.equal((await again.json()).error, 'username_already_changed');
  marie = portal('uid-marie', 'mariedup', 'Marie Dupont');
  const accepted = await fetch(`${base}/api/join/${token}/accept`, { method: 'POST', headers: as(marie), body: '{}' });
  assert.equal(accepted.status, 200);
  assert.equal((await fetch(`${base}/api/join/${token}`)).status, 404); // one person only
  // Marie is "a kid Albie brought in", so Albie's parent Mama has her in scope.
  const kids = await (await fetch(`${base}/api/manage/kids`, { headers: as(mama) })).json();
  assert.ok(kids.kids.some((k) => k.via === 'invited by albie'));
});

test('a parent invites a co-parent for their own kid only', async () => {
  const ok = await fetch(`${base}/api/manage/invites`, { method: 'POST', headers: as(mama), body: JSON.stringify({ kind: 'parent', name: 'Jean Tremblay', for_kid: 'albie' }) });
  assert.equal(ok.status, 201);
  const body = await ok.json();
  assert.match(body.qr_data_url, /^data:image\/png;base64,/); assert.equal(body.username, 'jean-t');
  const notMine = await fetch(`${base}/api/manage/invites`, { method: 'POST', headers: as(mama), body: JSON.stringify({ kind: 'parent', name: 'X Y', for_kid: 'juju' }) });
  assert.equal(notMine.status, 403);
});
