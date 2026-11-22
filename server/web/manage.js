// Parents' page: quiet times per kid device, the parent code, and invites. API: parent-controls.mjs.
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const NAMES = { chat: 'Chat', walkie: 'Walkie', music: 'Music', books: 'Books', podcasts: 'Podcasts' };
const state = document.querySelector('#state');
let features = [];

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', headers: { 'content-type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function el(tag, props = {}, ...children) { const node = Object.assign(document.createElement(tag), props); node.append(...children); return node; }

function windowEditor(win = { label: '', days: [0, 1, 2, 3, 4], start: '21:00', end: '07:00', features: ['chat', 'music', 'podcasts'] }) {
  const box = el('div', { className: 'window' });
  const times = el('div', { className: 'times' },
    el('input', { className: 'w-label', value: win.label, placeholder: 'School nights', maxLength: 40, ariaLabel: 'Name' }),
    el('label', {}, 'from ', el('input', { type: 'time', className: 'w-start', value: win.start })),
    el('label', {}, 'to ', el('input', { type: 'time', className: 'w-end', value: win.end })));
  const days = el('div', { className: 'days' }, ...DAYS.map((name, index) =>
    el('label', {}, el('input', { type: 'checkbox', className: 'w-day', value: index, checked: win.days.includes(index) }), name)));
  const feats = el('div', { className: 'features' }, el('span', { className: 'muted' }, 'Off: '), ...features.map((f) =>
    el('label', {}, el('input', { type: 'checkbox', className: 'w-feature', value: f, checked: win.features.includes(f) }), NAMES[f] || f)));
  const remove = el('button', { type: 'button', className: 'quiet-button remove', textContent: 'Remove' });
  remove.onclick = () => box.remove();
  box.append(times, days, feats, remove);
  return box;
}

function readWindows(node) {
  return [...node.querySelectorAll('.window')].map((box) => ({
    label: box.querySelector('.w-label').value,
    start: box.querySelector('.w-start').value, end: box.querySelector('.w-end').value,
    days: [...box.querySelectorAll('.w-day:checked')].map((c) => Number(c.value)),
    features: [...box.querySelectorAll('.w-feature:checked')].map((c) => c.value),
  }));
}

function renderKid(kid) {
  const node = document.querySelector('#kid-template').content.firstElementChild.cloneNode(true);
  node.querySelector('.kid-name').textContent = kid.display_name;
  node.querySelector('.kid-via').textContent = kid.via === 'own' || kid.via === 'admin' ? '' : kid.via;
  node.querySelector('.kid-parents').textContent = kid.guardians.length ? `Parents: ${kid.guardians.map((g) => g.display_name).join(', ')}` : 'No parent set yet';
  if (kid.policy) {
    const policy = node.querySelector('.policy'); policy.hidden = false;
    const windows = node.querySelector('.windows');
    kid.policy.blackouts.forEach((w) => windows.append(windowEditor(w)));
    node.querySelector('.add-window').onclick = () => windows.append(windowEditor());
    node.querySelector('.clock-override').checked = kid.policy.clock_override_enabled;
    node.querySelector('.device-settings').checked = kid.policy.device_settings_enabled;
    node.querySelector('.code-state').textContent = kid.policy.has_parent_code ? 'A parent code is set. Type a new one to replace it.' : 'No parent code yet.';
    node.querySelector('.save').onclick = async () => {
      const out = node.querySelector('.save-state'); out.textContent = 'Saving…';
      try {
        const code = node.querySelector('.parent-code').value.trim();
        const result = await api(`api/manage/kids/${encodeURIComponent(kid.id)}/policy`, { method: 'PUT', body: JSON.stringify({
          blackouts: readWindows(windows), parent_code: code || undefined,
          clock_override_enabled: node.querySelector('.clock-override').checked,
          device_settings_enabled: node.querySelector('.device-settings').checked }) });
        node.querySelector('.parent-code').value = '';
        node.querySelector('.code-state').textContent = result.policy.has_parent_code ? 'A parent code is set. Type a new one to replace it.' : 'No parent code yet.';
        out.textContent = 'Saved. The device picks it up next time it is on Wi-Fi.';
      } catch (error) { out.textContent = `Not saved: ${error.message.replaceAll('_', ' ')}`; }
    };
  }
  node.querySelector('.invite-form').onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target; const data = Object.fromEntries(new FormData(form));
    try {
      const body = data.kind === 'parent' ? { kind: 'parent', name: data.name, for_kid: kid.id } : { kind: 'kid', name: data.name, group_id: kid.groups[0]?.id };
      const result = await api('api/manage/invites', { method: 'POST', body: JSON.stringify(body) });
      const box = node.querySelector('.invite-result'); box.hidden = false;
      box.querySelector('.invite-qr').src = result.qr_data_url;
      box.querySelector('.invite-username').textContent = result.username;
      Object.assign(box.querySelector('.invite-link'), { href: result.url, textContent: result.url });
    } catch (error) { const box = node.querySelector('.invite-result'); box.hidden = false; box.replaceChildren(el('p', { className: 'muted' }, `Invite not made: ${error.message.replaceAll('_', ' ')}`)); }
  };
  return node;
}

async function load() {
  try {
    const data = await api('api/manage/kids');
    features = data.features;
    const kids = document.querySelector('#kids'); kids.replaceChildren(...data.kids.map(renderKid));
    state.textContent = data.kids.length ? '' : 'No kids are linked to your account yet. Ask the family admin to add you as a parent.';
    const list = document.querySelector('#invites');
    list.replaceChildren(...data.invites.map((i) => el('li', {}, `${i.display_name} (${i.chosen_username || i.default_username}), ${i.kind}, until ${new Date(i.expires_at).toLocaleDateString()}`)));
    document.querySelector('#invites-panel').hidden = !data.invites.length;
  } catch (error) { state.textContent = `Could not load: ${error.message}`; }
}
load();
