// Join page: a phone that scanned the QR or a computer that opened the link. The username was
// reserved when the invite was made; the person only sets a password (and may rename once).
const token = location.pathname.split('/').pop();
const $ = (id) => document.getElementById(id);
const say = (text) => { $('error').textContent = text; };
const pretty = (code) => ({ invite_invalid: 'This invite was used, cancelled or has expired. Ask for a new one.',
  username_taken: 'That username is taken. Try another.', username_already_changed: 'The username was already changed once.',
  invalid_username: 'Use 2–32 letters, digits, dots, dashes or underscores.', unauthorized: 'Sign in first, then press the button again.' }[code] || code.replaceAll('_', ' '));

async function call(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', headers: { 'content-type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function accept() {
  try {
    const result = await call(`../api/join/${token}/accept`, { method: 'POST', body: '{}' });
    $('lede').textContent = `You’re in ${result.group}.`;
    $('enroll').hidden = true; $('have-account').hidden = true; $('install').hidden = true;
    setTimeout(() => { location.href = result.kind === 'parent' ? '../manage' : '../'; }, 1200);
  } catch (error) {
    if (error.message === 'unauthorized') location.href = `../login?next=${encodeURIComponent(location.pathname)}`;
    else say(pretty(error.message));
  }
}

async function start() {
  let invite;
  try { invite = await call(`../api/join/${token}`); } catch (error) { $('lede').textContent = pretty(error.message); return; }
  if (new URLSearchParams(location.search).has('done')) { $('lede').textContent = 'Finishing…'; return accept(); }
  const from = invite.from ? `${invite.from} invited you` : 'You’re invited';
  $('lede').textContent = invite.kind === 'parent'
    ? `${from} to help look after their SupaChat devices in ${invite.group}, ${invite.name}.`
    : `${from} to chat in ${invite.group}, ${invite.name}.`;
  const ua = navigator.userAgent;
  const phone = /Android/i.test(ua) ? 'android' : /iPhone|iPad|iPod/i.test(ua) ? 'ios' : '';
  if (phone && invite.install?.[phone]) {
    $('install').hidden = false;
    Object.assign($(phone), { hidden: false, href: invite.install[phone] });
  }
  $('username').value = invite.username;
  $('username').readOnly = !invite.can_change_username;
  if (!invite.can_change_username) $('username-help').textContent = 'This username is set.';
  $('enroll').hidden = false; $('have-account').hidden = false;
}

$('enroll').onsubmit = async (event) => {
  event.preventDefault(); say('');
  try {
    const result = await call(`../api/join/${token}/enroll`, { method: 'POST', body: JSON.stringify({ username: $('username').value.trim().toLowerCase() }) });
    location.href = result.url;
  } catch (error) { say(pretty(error.message)); }
};
$('accept').onclick = accept;
start();
