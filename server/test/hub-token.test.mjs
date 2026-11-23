// Ben's hub signs in as Papa with its own key (SUPACHAT_PAPA_HUB_TOKEN_HASH), beside Papa's device token.
// SUPACHAT_TEST_SERVER_ROOT runs these checks against another copy of the server (e.g. the deployed one).
import assert from 'node:assert/strict';
import { createHash, randomBytes, scryptSync } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';

const serverRoot = process.env.SUPACHAT_TEST_SERVER_ROOT || join(import.meta.dirname, '..');
const sha = (value) => createHash('sha256').update(value).digest('hex');
const albieToken = randomBytes(24).toString('hex');
const papaDeviceToken = randomBytes(24).toString('hex');
const hubToken = randomBytes(32).toString('base64url');
const salt = randomBytes(16);
const passwordHash = `scrypt$${salt.toString('hex')}$${scryptSync('pw', salt, 32).toString('hex')}`;
const servers = [];

async function start(port, extraEnv) {
  const dataDir = mkdtempSync(join(tmpdir(), 'supachat-hub-test-'));
  const child = spawn(process.execPath, ['--experimental-sqlite', 'src/server.mjs'], {
    cwd: serverRoot,
    env: {
      PATH: process.env.PATH,
      SUPACHAT_HOST: '127.0.0.1', SUPACHAT_PORT: String(port), SUPACHAT_DATA_DIR: dataDir,
      SUPACHAT_PUBLIC_BASE: '/supachat', SUPACHAT_SESSION_SECRET: randomBytes(32).toString('hex'),
      SUPACHAT_PAPA_PASSWORD_HASH: passwordHash,
      SUPACHAT_ALBIE_DEVICE_TOKEN_HASH: sha(albieToken),
      SUPACHAT_PAPA_DEVICE_TOKEN_HASH: sha(papaDeviceToken),
      SUPACHAT_PORTAL_HOST: 'supachat.net', SUPACHAT_PAPA_AUTHENTIK_USERNAME: 'papa',
      ...extraEnv,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  servers.push({ child, dataDir });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('server start timeout')), 5000);
    child.stdout.on('data', (chunk) => { if (String(chunk).includes('SupaChat listening')) { clearTimeout(timer); resolve(); } });
    child.on('exit', (code) => reject(new Error(`server exited ${code}`)));
  });
  return `http://127.0.0.1:${port}`;
}

const session = (base, token) => fetch(`${base}/api/session`, { headers: token == null ? {} : { authorization: `Bearer ${token}` } });

let withHub;
let withoutHub;
test.before(async () => {
  withHub = await start(20194, { SUPACHAT_PAPA_HUB_TOKEN_HASH: sha(hubToken) });
  withoutHub = await start(20195, {});
});

test.after(async () => {
  for (const { child, dataDir } of servers) {
    if (child.exitCode === null) { const exited = new Promise((r) => child.once('exit', r)); child.kill(); await exited; }
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test('(a) Papa device token is still accepted as Papa, with or without a hub key configured', async () => {
  for (const base of [withHub, withoutHub]) {
    const response = await session(base, papaDeviceToken);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.user.id, 'papa');
    assert.equal(body.auth, 'device');
  }
});

test('(b) hub key is accepted as Papa, with exactly Papa\'s rooms, and can post in family', async () => {
  const hub = await (await session(withHub, hubToken)).json();
  const device = await (await session(withHub, papaDeviceToken)).json();
  assert.equal(hub.user.id, 'papa');
  assert.deepEqual(hub.rooms.map((r) => r.id).sort(), device.rooms.map((r) => r.id).sort());
  assert.ok(hub.rooms.some((r) => r.id === 'family'));
  const posted = await fetch(`${withHub}/api/messages`, {
    method: 'POST', headers: { authorization: `Bearer ${hubToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ room_id: 'family', body: 'hub test', client_id: 'ben-action-test' }),
  });
  assert.equal(posted.status, 201);
  assert.equal((await posted.json()).message.author_id, 'papa');
});

test('(c) invalid, empty, other people\'s and unconfigured keys are rejected or keep their own identity', async () => {
  for (const token of [randomBytes(32).toString('base64url'), '', sha(hubToken), sha(papaDeviceToken)]) {
    assert.equal((await session(withHub, token)).status, 401, 'unknown key must be rejected');
  }
  assert.equal((await session(withHub, null)).status, 401);
  assert.equal((await session(withoutHub, hubToken)).status, 401, 'hub key must not work when no hub hash is configured');
  const albie = await (await session(withHub, albieToken)).json();
  assert.equal(albie.user.id, 'albie', 'kids keep their own identity');
});
