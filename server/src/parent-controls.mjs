// Parent controls: parents (guardians) of kids, per-device blackout policy, person invites with a
// reserved default username and a QR code. Design and trust model: docs/PARENT-CONTROLS.md.
// Nothing here touches the message path: server.mjs calls initParentControls() once, then
// handleParentControls() just before its own routes.
import { createHash, createHmac, pbkdf2Sync, randomBytes, timingSafeEqual } from 'node:crypto';

export const FEATURES = ['chat', 'walkie', 'music', 'books', 'podcasts'];
// Never blacked out: the device charges beside the kid and must stay a clock.
export const ALWAYS_ALLOWED = ['clock', 'alarms', 'reminders', 'timers', 'screen_time'];
export const DEVICE_TZ = 'EST5EDT,M3.2.0/2,M11.1.0/2';
const CODE_ITERATIONS = 20000;
const INVITE_DAYS = 14;
const MAX_BLACKOUTS = 12;
// A signed policy stays in force offline. valid_until only says when the device should start
// showing "policy not refreshed" in its status line; it never lifts a restriction.
const POLICY_REFRESH_DAYS = 30;
// Clock-trust tiers (docs/PARENT-CONTROLS.md, "Clock trust"). Sent with the policy so they can be
// tuned without a firmware build. Tier C (unknown or wrong clock) never blocks media.
export const CLOCK_TRUST = {
  checkin_minutes: 20,      // fetch policy + signed time this often while on a known network, and at boot
  synced_hours: 6,          // tier A: enforce windows exactly
  plausible_hours: 72,      // tier B: enforce, starting each window grace_minutes late
  grace_minutes: 10,
  rollback_tolerance_minutes: 2, // clock behind the high-water mark by more than this: move it forward to the mark
  unknown: 'allow_media',   // tier C: chat stays under the windows (it needs Wi-Fi, which brings a sync), media plays
};

let db;
const joinAttempts = new Map();

export function initParentControls(database) {
  db = database;
  db.exec(`
    CREATE TABLE IF NOT EXISTS guardians (
      parent_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      kid_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      added_by TEXT,
      added_at INTEGER NOT NULL,
      PRIMARY KEY (parent_id, kid_id)
    );
    CREATE TABLE IF NOT EXISTS kid_links (
      kid_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      invited_by_kid TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      linked_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_policies (
      user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      blackouts TEXT NOT NULL DEFAULT '[]',
      clock_override_enabled INTEGER NOT NULL DEFAULT 0,
      device_settings_enabled INTEGER NOT NULL DEFAULT 0,
      parent_code_hash TEXT,
      updated_at INTEGER NOT NULL,
      updated_by TEXT
    );
    CREATE TABLE IF NOT EXISTS person_invites (
      id TEXT PRIMARY KEY,
      token_hash TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL CHECK (kind IN ('parent', 'kid')),
      display_name TEXT NOT NULL,
      default_username TEXT NOT NULL,
      chosen_username TEXT,
      group_id TEXT NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
      for_kid TEXT REFERENCES users(id) ON DELETE CASCADE,
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,
      claimed_at INTEGER,
      claimed_by TEXT,
      revoked_at INTEGER
    );
  `);
}

const sha = (text) => createHash('sha256').update(text).digest('hex');
function isAdmin(userId) { return db.prepare('SELECT role FROM users WHERE id = ?').get(userId)?.role === 'admin'; }
function kindOf(userId) { return db.prepare('SELECT kind FROM users WHERE id = ?').get(userId)?.kind; }

// Jay's scope rule: a parent manages their own kids, plus any kid one of their kids brought in.
export function kidsInScope(parentId) {
  if (isAdmin(parentId)) return db.prepare("SELECT id FROM users WHERE kind = 'device' ORDER BY display_name").all().map((r) => ({ id: r.id, via: 'admin' }));
  const own = db.prepare('SELECT kid_id FROM guardians WHERE parent_id = ?').all(parentId).map((r) => r.kid_id);
  const out = own.map((id) => ({ id, via: 'own' }));
  for (const kid of own) {
    for (const row of db.prepare('SELECT kid_id FROM kid_links WHERE invited_by_kid = ?').all(kid)) {
      if (!out.some((k) => k.id === row.kid_id)) out.push({ id: row.kid_id, via: `invited by ${kid}` });
    }
  }
  return out;
}
export function managesKid(parentId, kidId) { return kidsInScope(parentId).some((k) => k.id === kidId); }

// Default usernames come from the typed name: "Marie Dupont" -> "marie-d", then "marie-d2"...
export function defaultUsername(name) {
  const parts = String(name).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9 ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  let base = parts.length ? (parts.length > 1 ? `${parts[0]}-${parts[parts.length - 1][0]}` : parts[0]) : 'friend';
  base = base.slice(0, 28);
  if (base.length < 2) base = `${base}x`;
  for (let n = 1; n < 1000; n++) {
    const candidate = n === 1 ? base : `${base}${n}`;
    if (!usernameTaken(candidate)) return candidate;
  }
  return `${base}-${randomBytes(3).toString('hex')}`;
}
function usernameTaken(username, exceptInvite = '') {
  return !!db.prepare('SELECT 1 FROM users WHERE id = ?').get(username)
    || !!db.prepare(`SELECT 1 FROM person_invites WHERE id <> ? AND revoked_at IS NULL AND (claimed_at IS NOT NULL OR expires_at > ?)
      AND (default_username = ? OR chosen_username = ?)`).get(exceptInvite, Date.now(), username, username);
}

const hhmm = /^([01]\d|2[0-3]):[0-5]\d$/;
export function validateBlackouts(input) {
  if (!Array.isArray(input) || input.length > MAX_BLACKOUTS) return null;
  const out = [];
  for (const item of input) {
    if (!item || typeof item !== 'object') return null;
    const label = String(item.label || '').trim().slice(0, 40);
    const days = [...new Set((Array.isArray(item.days) ? item.days : []).map(Number))].sort();
    const features = [...new Set(Array.isArray(item.features) ? item.features.map(String) : [])];
    const start = String(item.start || ''); const end = String(item.end || '');
    if (!days.length || !days.every((d) => Number.isInteger(d) && d >= 0 && d <= 6)) return null;
    if (!features.length || !features.every((f) => FEATURES.includes(f))) return null;
    if (!hhmm.test(start) || !hhmm.test(end) || start === end) return null;
    out.push({ label, days, start, end, features: FEATURES.filter((f) => features.includes(f)) });
  }
  return out;
}

// A window that crosses midnight (21:30-07:00) belongs to the day it starts on. The firmware
// will carry the same function; this one is the reference its host test compares against.
export function blockedFeatures(blackouts, weekday, minuteOfDay) {
  const blocked = new Set();
  const minutes = (text) => Number(text.slice(0, 2)) * 60 + Number(text.slice(3));
  for (const window of blackouts) {
    const start = minutes(window.start); const end = minutes(window.end);
    const today = window.days.includes(weekday) && (start < end ? minuteOfDay >= start && minuteOfDay < end : minuteOfDay >= start);
    const fromYesterday = start > end && window.days.includes((weekday + 6) % 7) && minuteOfDay < end;
    if (today || fromYesterday) for (const feature of window.features) blocked.add(feature);
  }
  return FEATURES.filter((f) => blocked.has(f));
}

export function hashParentCode(code, salt = randomBytes(16)) {
  const hash = pbkdf2Sync(String(code), salt, CODE_ITERATIONS, 32, 'sha256');
  return `pbkdf2-sha256$${CODE_ITERATIONS}$${salt.toString('hex')}$${hash.toString('hex')}`;
}
export function verifyParentCode(code, stored) {
  const [scheme, iterations, salt, hash] = String(stored || '').split('$');
  if (scheme !== 'pbkdf2-sha256' || !salt || !hash) return false;
  const actual = pbkdf2Sync(String(code), Buffer.from(salt, 'hex'), Number(iterations), 32, 'sha256');
  return timingSafeEqual(actual, Buffer.from(hash, 'hex'));
}

function policyRow(deviceId) { return db.prepare('SELECT * FROM device_policies WHERE user_id = ?').get(deviceId) || null; }
function publicPolicy(row) {
  return {
    blackouts: row ? JSON.parse(row.blackouts) : [],
    clock_override_enabled: !!row?.clock_override_enabled,
    device_settings_enabled: !!row?.device_settings_enabled,
    has_parent_code: !!row?.parent_code_hash,
    updated_at: row?.updated_at || null, updated_by: row?.updated_by || null,
  };
}

// Applies a policy edit from the portal, the app, or (with the parent code) the device itself.
function savePolicy(deviceId, payload, by) {
  const blackouts = validateBlackouts(payload.blackouts);
  if (!blackouts) return { status: 400, error: 'invalid_blackouts' };
  const current = policyRow(deviceId);
  let codeHash = current?.parent_code_hash || null;
  if (payload.parent_code !== undefined && payload.parent_code !== null && payload.parent_code !== '') {
    if (!/^\d{6,8}$/.test(String(payload.parent_code))) return { status: 400, error: 'invalid_parent_code' };
    codeHash = hashParentCode(String(payload.parent_code));
  }
  if (payload.clear_parent_code === true) codeHash = null;
  const clockOverride = payload.clock_override_enabled === true;
  const deviceSettings = payload.device_settings_enabled === true;
  if ((clockOverride || deviceSettings) && !codeHash) return { status: 400, error: 'parent_code_required' };
  db.prepare(`INSERT INTO device_policies(user_id, blackouts, clock_override_enabled, device_settings_enabled, parent_code_hash, updated_at, updated_by)
    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET blackouts = excluded.blackouts,
    clock_override_enabled = excluded.clock_override_enabled, device_settings_enabled = excluded.device_settings_enabled,
    parent_code_hash = excluded.parent_code_hash, updated_at = excluded.updated_at, updated_by = excluded.updated_by`)
    .run(deviceId, JSON.stringify(blackouts), clockOverride ? 1 : 0, deviceSettings ? 1 : 0, codeHash, Date.now(), by);
  return { policy: publicPolicy(policyRow(deviceId)) };
}

// What the device receives: the policy and the server's time, signed with a key only that device
// and the server hold (sha256 of its bearer token, which the server already stores). The device
// checks the signature and keeps the newest server_time it has seen, so a replayed answer can't
// wind its clock back.
export function devicePolicyEnvelope(deviceId, tokenHash, now = Date.now()) {
  const row = policyRow(deviceId);
  const [, iterations, salt, hash] = String(row?.parent_code_hash || '').split('$');
  const payload = JSON.stringify({
    v: 1, user: deviceId, server_time: now, issued_at: now, valid_until: now + POLICY_REFRESH_DAYS * 24 * 3600_000,
    tz: DEVICE_TZ, clock_trust: CLOCK_TRUST,
    always_allowed: ALWAYS_ALLOWED,
    blackouts: row ? JSON.parse(row.blackouts) : [],
    clock_override: !!row?.clock_override_enabled && !!hash,
    device_settings: !!row?.device_settings_enabled && !!hash,
    parent_code: hash ? { kdf: 'pbkdf2-sha256', iterations: Number(iterations), salt, hash } : null,
    policy_updated_at: row?.updated_at || 0,
  });
  return { payload, sig: createHmac('sha256', tokenHash).update(payload).digest('hex') };
}

function inviteFor(token) {
  const invite = db.prepare('SELECT * FROM person_invites WHERE token_hash = ?').get(sha(token));
  if (!invite || invite.revoked_at || invite.claimed_at || invite.expires_at <= Date.now()) return null;
  return invite;
}

function createInvite({ kind, name, groupId, forKid, by }) {
  const displayName = String(name || '').trim();
  if (!displayName || displayName.length > 80) return { status: 400, error: 'invalid_name' };
  const token = randomBytes(24).toString('base64url');
  const id = randomBytes(8).toString('hex');
  const username = defaultUsername(displayName);
  const expiresAt = Date.now() + INVITE_DAYS * 24 * 3600_000;
  db.prepare(`INSERT INTO person_invites(id, token_hash, kind, display_name, default_username, group_id, for_kid, created_by, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(id, sha(token), kind, displayName, username, groupId, forKid || null, by, Date.now(), expiresAt);
  return { id, token, default_username: username, expires_at: expiresAt };
}

// ctx: { json, body, sameOrigin, actor, webUser, deviceUser, deviceTokenHash, createInvitation,
//        QRCode, portalHost, base, pages: { manage, join }, installUrls: { android, ios } }
export async function handleParentControls(req, res, url, ctx) {
  const { json } = ctx;
  const send = (status, value) => { json(res, status, value); return true; };
  const html = (content) => { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); res.end(content); return true; };
  const joinUrl = (token) => `https://${ctx.portalHost}/join/${token}`;

  // Public: the page a scanned QR (phone) or a pasted link (computer) opens, and its calls.
  if (/^\/join\/[A-Za-z0-9_-]{20,64}$/.test(url.pathname) && req.method === 'GET') return html(ctx.pages.join);
  const joinApi = url.pathname.match(/^\/api\/join\/([A-Za-z0-9_-]{20,64})(?:\/(enroll|accept))?$/);
  if (joinApi) {
    const invite = inviteFor(joinApi[1]);
    if (!invite) return send(404, { error: 'invite_invalid' });
    const group = db.prepare('SELECT name FROM user_groups WHERE id = ?').get(invite.group_id);
    const from = db.prepare('SELECT display_name FROM users WHERE id = ?').get(invite.created_by)?.display_name || '';
    const action = joinApi[2];
    if (!action && req.method === 'GET') {
      return send(200, { name: invite.display_name, kind: invite.kind, group: group?.name, from,
        username: invite.chosen_username || invite.default_username, can_change_username: !invite.chosen_username,
        expires_at: invite.expires_at, install: ctx.installUrls });
    }
    if (req.method !== 'POST') return send(405, { error: 'method_not_allowed' });
    if (!ctx.sameOrigin(req)) return send(403, { error: 'origin_rejected' });
    if (action === 'enroll') {
      // New account: the username is reserved already; the person sets a password in Authentik.
      // They may change the username once; the invite stays bound to its token, not the name.
      const ip = String(req.headers['x-forwarded-for'] || req.socket.remoteAddress || '').split(',')[0].trim();
      const recent = (joinAttempts.get(ip) || []).filter((t) => t > Date.now() - 3600_000);
      if (recent.length >= 10) return send(429, { error: 'try_later' });
      joinAttempts.set(ip, [...recent, Date.now()]);
      const payload = await ctx.body(req);
      let username = invite.chosen_username || invite.default_username;
      const wanted = String(payload.username || '').trim().toLowerCase();
      if (wanted && wanted !== username) {
        if (invite.chosen_username) return send(409, { error: 'username_already_changed' });
        if (!/^[a-z0-9][a-z0-9._-]{1,31}$/.test(wanted)) return send(400, { error: 'invalid_username' });
        if (usernameTaken(wanted, invite.id)) return send(409, { error: 'username_taken' });
        db.prepare('UPDATE person_invites SET chosen_username = ? WHERE id = ?').run(wanted, invite.id);
        username = wanted;
      }
      const next = `${joinUrl(joinApi[1])}?done=1`;
      const invitation = await ctx.createInvitation({ username, displayName: invite.display_name, next });
      if (invitation.error) return send(invitation.status, { error: invitation.error });
      return send(201, { url: invitation.url, username });
    }
    // accept: the person is signed in (new account just made, or one they already had).
    const user = ctx.actor(req);
    if (!user) return send(401, { error: 'unauthorized' });
    if (kindOf(user) === 'device') return send(403, { error: 'devices_cannot_join' });
    const now = Date.now();
    db.prepare('INSERT OR IGNORE INTO user_group_members(group_id, user_id) VALUES (?, ?)').run(invite.group_id, user);
    if (invite.kind === 'parent' && invite.for_kid) db.prepare('INSERT OR IGNORE INTO guardians(parent_id, kid_id, added_by, added_at) VALUES (?, ?, ?, ?)').run(user, invite.for_kid, invite.created_by, now);
    if (invite.kind === 'kid' && kindOf(invite.created_by) === 'device') db.prepare('INSERT OR IGNORE INTO kid_links(kid_id, invited_by_kid, linked_at) VALUES (?, ?, ?)').run(user, invite.created_by, now);
    db.prepare('UPDATE person_invites SET claimed_at = ?, claimed_by = ? WHERE id = ?').run(now, user, invite.id);
    ctx.afterJoin?.();
    return send(200, { ok: true, group: group?.name, kind: invite.kind });
  }

  if (url.pathname === '/manage' && req.method === 'GET') {
    if (!ctx.webUser(req)) { res.writeHead(303, { location: `${ctx.base(req)}/login` }); res.end(); return true; }
    return html(ctx.pages.manage);
  }

  // Devices: their own signed policy and time; a kid sharing with a friend; parent mode edits.
  if (url.pathname.startsWith('/api/device/policy') || url.pathname === '/api/device/invites') {
    const device = ctx.deviceUser(req);
    if (!device) return send(401, { error: 'unauthorized' });
    if (url.pathname === '/api/device/policy' && req.method === 'GET') return send(200, devicePolicyEnvelope(device, ctx.deviceTokenHash(device)));
    if (url.pathname === '/api/device/policy' && req.method === 'PUT') {
      // Parent mode on the device: last resort. The device sends the code the parent typed.
      const payload = await ctx.body(req, 16384);
      const row = policyRow(device);
      if (!row?.device_settings_enabled || !row.parent_code_hash) return send(403, { error: 'device_settings_disabled' });
      if (!verifyParentCode(String(payload.code || ''), row.parent_code_hash)) return send(403, { error: 'wrong_parent_code' });
      const result = savePolicy(device, { blackouts: payload.blackouts, clock_override_enabled: !!row.clock_override_enabled, device_settings_enabled: true }, `${device}:parent-mode`);
      return result.error ? send(result.status, { error: result.error }) : send(200, devicePolicyEnvelope(device, ctx.deviceTokenHash(device)));
    }
    if (url.pathname === '/api/device/invites' && req.method === 'POST') {
      // The kid types who they share with; the screen shows the link as a QR. No image here:
      // the device draws the QR itself and its JSON budget is small.
      const payload = await ctx.body(req);
      const groups = db.prepare('SELECT group_id FROM user_group_members WHERE user_id = ? ORDER BY group_id').all(device).map((r) => r.group_id);
      const groupId = payload.group_id ? String(payload.group_id) : groups[0];
      if (!groupId || !groups.includes(groupId)) return send(400, { error: 'invalid_group' });
      const invite = createInvite({ kind: 'kid', name: payload.name, groupId, by: device });
      return invite.error ? send(invite.status, { error: invite.error }) : send(201, { url: joinUrl(invite.token), username: invite.default_username, expires_at: invite.expires_at });
    }
    return send(405, { error: 'method_not_allowed' });
  }

  if (!url.pathname.startsWith('/api/manage/')) return false;
  if (ctx.deviceUser(req)) return send(403, { error: 'manage_from_portal_or_app' });
  const user = ctx.actor(req);
  if (!user) return send(401, { error: 'unauthorized' });
  if (req.method !== 'GET' && ctx.webUser(req) && !ctx.sameOrigin(req)) return send(403, { error: 'origin_rejected' });

  if (url.pathname === '/api/manage/kids' && req.method === 'GET') {
    const kids = kidsInScope(user).map(({ id, via }) => {
      const kid = db.prepare('SELECT id, display_name, kind FROM users WHERE id = ?').get(id);
      const guardians = db.prepare('SELECT u.id, u.display_name FROM guardians g JOIN users u ON u.id = g.parent_id WHERE g.kid_id = ? ORDER BY u.display_name').all(id);
      const groups = db.prepare('SELECT g.id, g.name FROM user_group_members gm JOIN user_groups g ON g.id = gm.group_id WHERE gm.user_id = ? ORDER BY g.name').all(id);
      return { ...kid, via, guardians, groups, policy: kid.kind === 'device' ? publicPolicy(policyRow(id)) : undefined };
    });
    const invites = db.prepare(`SELECT id, kind, display_name, default_username, chosen_username, group_id, for_kid, expires_at FROM person_invites
      WHERE created_by = ? AND claimed_at IS NULL AND revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC`).all(user, Date.now());
    return send(200, { features: FEATURES, always_allowed: ALWAYS_ALLOWED, kids, invites });
  }

  const policyPath = url.pathname.match(/^\/api\/manage\/kids\/([^/]+)\/policy$/);
  if (policyPath && req.method === 'PUT') {
    const kidId = decodeURIComponent(policyPath[1]);
    if (!managesKid(user, kidId) || kindOf(kidId) !== 'device') return send(403, { error: 'not_your_kid' });
    const result = savePolicy(kidId, await ctx.body(req, 16384), user);
    return result.error ? send(result.status, { error: result.error }) : send(200, result);
  }

  const guardianPath = url.pathname.match(/^\/api\/manage\/kids\/([^/]+)\/guardians\/([^/]+)$/);
  if (guardianPath && ['PUT', 'DELETE'].includes(req.method)) {
    const kidId = decodeURIComponent(guardianPath[1]); const parentId = decodeURIComponent(guardianPath[2]);
    // Only an admin or the kid's own parent changes who the kid's parents are.
    const own = isAdmin(user) || !!db.prepare('SELECT 1 FROM guardians WHERE parent_id = ? AND kid_id = ?').get(user, kidId);
    if (!own) return send(403, { error: 'not_your_kid' });
    if (req.method === 'PUT') {
      if (!kindOf(parentId) || kindOf(parentId) === 'device') return send(400, { error: 'invalid_parent' });
      db.prepare('INSERT OR IGNORE INTO guardians(parent_id, kid_id, added_by, added_at) VALUES (?, ?, ?, ?)').run(parentId, kidId, user, Date.now());
    } else {
      const count = db.prepare('SELECT COUNT(*) AS n FROM guardians WHERE kid_id = ?').get(kidId).n;
      if (count <= 1 && !isAdmin(user)) return send(409, { error: 'last_parent' });
      db.prepare('DELETE FROM guardians WHERE parent_id = ? AND kid_id = ?').run(parentId, kidId);
    }
    return send(200, { ok: true });
  }

  if (url.pathname === '/api/manage/invites' && req.method === 'POST') {
    const payload = await ctx.body(req);
    const kind = payload.kind === 'kid' ? 'kid' : 'parent';
    let groupId = String(payload.group_id || ''); let forKid = null;
    if (kind === 'parent') {
      // A co-parent for one of my own kids, joined to that kid's group.
      forKid = String(payload.for_kid || '');
      const own = isAdmin(user) || !!db.prepare('SELECT 1 FROM guardians WHERE parent_id = ? AND kid_id = ?').get(user, forKid);
      if (!own) return send(403, { error: 'not_your_kid' });
      const kidGroups = db.prepare('SELECT group_id FROM user_group_members WHERE user_id = ? ORDER BY group_id').all(forKid).map((r) => r.group_id);
      if (!groupId) groupId = kidGroups[0];
      if (!groupId || !kidGroups.includes(groupId)) return send(400, { error: 'invalid_group' });
    } else {
      const inScope = isAdmin(user) || kidsInScope(user).some((k) => db.prepare('SELECT 1 FROM user_group_members WHERE user_id = ? AND group_id = ?').get(k.id, groupId));
      if (!inScope) return send(403, { error: 'not_your_group' });
    }
    const invite = createInvite({ kind, name: payload.name, groupId, forKid, by: user });
    if (invite.error) return send(invite.status, { error: invite.error });
    const link = joinUrl(invite.token);
    const qrDataUrl = await ctx.QRCode.toDataURL(link, { errorCorrectionLevel: 'M', margin: 2, width: 320, color: { dark: '#10200f', light: '#f6f0dc' } });
    return send(201, { id: invite.id, url: link, qr_data_url: qrDataUrl, username: invite.default_username, expires_at: invite.expires_at });
  }
  const revokePath = url.pathname.match(/^\/api\/manage\/invites\/([a-f0-9]{16})$/);
  if (revokePath && req.method === 'DELETE') {
    db.prepare('UPDATE person_invites SET revoked_at = ? WHERE id = ? AND created_by = ?').run(Date.now(), revokePath[1], user);
    return send(200, { ok: true });
  }
  return send(404, { error: 'not_found' });
}
