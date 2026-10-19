// Arm Studio client. The Python service owns the motors; this page only reads state and queues actions.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper'];
const LABELS = {shoulder_pan: 'Base swivel', shoulder_lift: 'Shoulder lift', elbow_flex: 'Elbow', wrist_flex: 'Wrist bend', wrist_roll: 'Wrist twist', gripper: 'Gripper'};
const MODES = {
  disconnected: {label: 'Disconnected', tag: 'OFFLINE'},
  ready: {label: 'Ready · released', tag: 'READY'},
  holding: {label: 'Holding pose', tag: 'HOLDING', cls: 'good'},
  teleoperation: {label: 'Following leader', tag: 'FOLLOWING', cls: 'good'},
  calibrating: {label: 'Calibrating', tag: 'CALIBRATING', cls: 'warn'},
  error: {label: 'Needs attention', tag: 'ATTENTION', cls: 'warn'},
};
const PAGE_NAMES = {operate: 'Operate', sessions: 'Motion sessions', cameras: 'Cameras', learning: 'Learning', settings: 'Robot settings'};

const app = {token: null, modules: [], state: null, online: null, control: 'full', page: 'operate',
             dismissedError: null, settingsDirty: false, failures: 0,
             speeds: null, speedHold: {}};

// ---------- helpers ----------
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = (v, digits = 1) => (typeof v === 'number' && isFinite(v) ? v.toFixed(digits) : '—');
const unit = joint => (joint === 'gripper' ? '%' : '°');
const clock = iso => new Date(iso).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}
function patch(el, html) {
  if (el && el._html !== html) { el.innerHTML = html; el._html = html; }
}
function toggleClass(el, cls, on) {
  if (el) el.classList.toggle(cls, !!on);
}

let toastTimer;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Control-Token': app.token || ''},
    body: JSON.stringify(body),
  };
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function act(action, body = {}, message) {
  try {
    await api('/api/' + action, body);
    if (message) toast(message);
    setTimeout(poll, 60);
  } catch (err) {
    showBanner(err.message);
  }
}

function showBanner(text) {
  const el = $('#error-banner');
  el.textContent = text + '  ·  Click to dismiss';
  el.hidden = false;
  app.dismissedError = null;
}

// ---------- modal ----------
function modal({eyebrow = 'ROBOT CONTROL', title, html = '', ack, confirm = 'Continue', cancel = 'Cancel', read}) {
  const dialog = $('#modal');
  setText($('#modal-eyebrow'), eyebrow);
  setText($('#modal-title'), title);
  $('#modal-content').innerHTML = html + (ack ? `<label><input type="checkbox" id="modal-ack"> ${esc(ack)}</label>` : '');
  $('#modal-actions').innerHTML = `<button class="button ghost" data-result="cancel">${esc(cancel)}</button>` +
    `<button class="button primary" data-result="ok"${ack ? ' disabled' : ''}>${esc(confirm)}</button>`;
  const okButton = $('[data-result="ok"]', dialog);
  if (ack) $('#modal-ack').addEventListener('change', e => { okButton.disabled = !e.target.checked; });
  return new Promise(resolve => {
    let value = null;
    const done = result => {
      if (result === 'ok') {
        try { value = read ? read(dialog) : true; } catch (err) { toast(err.message); return; }
      }
      dialog.close();
    };
    dialog.onclick = e => {
      const button = e.target.closest('[data-result]');
      if (button && !button.disabled) done(button.dataset.result);
    };
    dialog.onclose = () => resolve(value);
    dialog.showModal();
  });
}

// ---------- robot schematic ----------
function armGeometry(p) {
  const rad = d => (d * Math.PI) / 180;
  const pan = Math.cos(rad(p.shoulder_pan ?? 0));
  const a1 = -90 + (p.shoulder_lift ?? 0);
  const a2 = a1 + 90 + (p.elbow_flex ?? 0);
  const a3 = a2 + (p.wrist_flex ?? 0);
  // Side view; base swivel foreshortens horizontal reach.
  const step = (o, angle, len) => [o[0] + len * Math.cos(rad(angle)) * pan, o[1] + len * Math.sin(rad(angle))];
  const s = [280, 214];
  const e = step(s, a1, 82);
  const w = step(e, a2, 74);
  const t = step(w, a3, 30);
  const open = 5 + (p.gripper ?? 25) * 0.25;
  return {s, e, w, t, j1: step(t, a3 - open, 22), j2: step(t, a3 + open, 22)};
}
const pts = list => list.map(q => q.map(v => v.toFixed(1)).join(',')).join(' ');

function drawRobot(state) {
  const connected = state?.connected;
  const follower = state?.follower?.positions || {};
  const leader = state?.leader?.positions || {};
  const hasFollower = connected && Object.keys(follower).length;
  const hasLeader = connected && Object.keys(leader).length;
  const f = armGeometry(hasFollower ? follower : {});
  let links = '';
  if (hasLeader) {
    const l = armGeometry(leader);
    links += `<polyline points="${pts([l.s, l.e, l.w, l.t])}" fill="none" stroke="#a7c5f8" stroke-width="3" stroke-dasharray="6 6" stroke-linecap="round" stroke-linejoin="round" opacity=".55"/>` +
      `<polyline points="${pts([l.j1, l.t, l.j2])}" fill="none" stroke="#a7c5f8" stroke-width="2" opacity=".55"/>`;
  }
  const dim = hasFollower ? 1 : 0.3;
  links += `<g opacity="${dim}"><polyline points="${pts([f.s, f.e, f.w, f.t])}" fill="none" stroke="#0a100e" stroke-width="18" stroke-linecap="round" stroke-linejoin="round" opacity=".5" transform="translate(4 6)"/>` +
    `<polyline points="${pts([f.s, f.e, f.w, f.t])}" fill="none" stroke="url(#arm-gradient)" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<polyline points="${pts([f.j1, f.t, f.j2])}" fill="none" stroke="#c9d6cf" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></g>`;
  const joints = [f.s, f.e, f.w].map(([x, y]) =>
    `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7.5" fill="#18211d" stroke="${hasFollower ? '#bcf4cf' : '#53645b'}" stroke-width="2"/>`).join('');
  patch($('#arm-links'), links);
  patch($('#arm-joints'), `<g opacity="${dim}">${joints}</g>`);
}

// ---------- page renderers ----------
function renderChrome(s) {
  const mode = MODES[s.mode] || MODES.disconnected;
  const conn = $('#connection');
  toggleClass(conn, 'online', s.connected);
  patch(conn, `<i></i>${s.connected ? 'Arms connected' : 'Not connected'}`);
  const tag = $('#mode-tag');
  setText(tag, mode.tag);
  tag.className = 'state-tag' + (mode.cls ? ' ' + mode.cls : '');
  setText($('#footer-status'), `${app.demo ? 'Demo service' : 'Local control service'} online · ${mode.label}`);

  const banner = $('#error-banner');
  if (s.error && s.error !== app.dismissedError) {
    banner.textContent = s.error + '  ·  Click to dismiss';
    banner.dataset.error = s.error;
    banner.hidden = false;
  } else if (banner.dataset.error && !s.error) {
    banner.hidden = true;
    banner.dataset.error = '';
  }
}

function usbStatus(s, role) {
  if (s.connected && s[role]?.port) return s[role].calibrated ? 'Connected' : 'Connected · uncalibrated';
  const serial = s.profile?.[role]?.serial;
  const port = s.profile?.[role]?.port;
  return (s.ports || []).some(p => (serial && p.serial === serial) || (!serial && port && p.device === port)) ? 'USB detected' : 'USB not found';
}

function renderOperate(s) {
  const following = s.mode === 'teleoperation';
  const calibrating = s.mode === 'calibrating';
  const calibrated = s.connected && s.leader?.calibrated && s.follower?.calibrated;
  drawRobot(s);

  setText($('#live-tag'), s.connected ? (following ? `LIVE · ${fmt(s.fps, 0)} HZ` : 'LIVE') : 'WAITING FOR CONNECTION');
  const f = s.follower?.positions || {};
  setText($('#pose-caption'), s.connected && Object.keys(f).length
    ? `Follower · lift ${fmt(f.shoulder_lift, 0)}° · elbow ${fmt(f.elbow_flex, 0)}° · grip ${fmt(f.gripper, 0)}%`
    : 'Connect to view joint positions');
  setText($('#leader-state'), usbStatus(s, 'leader'));
  setText($('#follower-state'), usbStatus(s, 'follower'));

  const connect = $('#connect');
  setText(connect, s.connected ? '⏏ Disconnect arms' : '↔ Connect arms');
  toggleClass(connect, 'busy', s.busy);

  // Teleoperation panel
  $$('#control-mode button').forEach(b => {
    toggleClass(b, 'selected', b.dataset.control === (following ? s.control : app.control));
    b.disabled = following;
  });
  const travel = s.profile?.starter_travel ?? 10;
  const speeds = Object.values(s.profile?.joint_speeds || {});
  const control = following ? s.control : app.control;
  setText($('#control-description'), control === 'full'
    ? 'The follower copies the leader across its calibrated joint ranges.'
    : `The follower copies changes from the leader’s starting pose, up to ${fmt(travel, 0)}° each way, at a gentle pace.`);
  patch($('#rate'), `${following ? fmt(s.fps, 0) : '—'}<span>Hz</span>`);
  const lo = Math.min(...speeds), hi = Math.max(...speeds);
  patch($('#speed'), control === 'starter' || !speeds.length ? '10<span>°/s</span>' : `${fmt(lo, 0)}${hi > lo ? '–' + fmt(hi, 0) : ''}<span>°/s</span>`);
  renderSpeeds(s, following && s.control === 'starter');
  toggleClass($('#check-connect'), 'good', s.connected);
  toggleClass($('#check-calibration'), 'good', calibrated);
  toggleClass($('#check-hold'), 'good', s.holding);

  const start = $('#start');
  setText(start, following ? '■ Stop following' : '▶ Start teleoperation');
  start.disabled = !following && !(s.connected && calibrated && s.holding && !calibrating);
  const hold = $('#hold');
  setText(hold, s.holding ? '✓ Holding' : '◇ Enable holding');
  hold.disabled = !s.connected || s.holding || calibrating;
  $('#release').disabled = !s.connected || !s.holding || calibrating;

  // Metrics
  const mode = MODES[s.mode] || MODES.disconnected;
  setText($('#state-metric'), mode.label);
  patch($('#voltage'), `${fmt(s.health?.voltage)} <span>V</span>`);
  patch($('#temperature'), `${fmt(s.health?.temperature, 0)} <span>°C</span>`);
  setText($('#cal-metric'), !s.connected ? 'Not checked' : calibrating ? 'In progress' : calibrated ? 'Verified' : 'Needs attention');

  // Joint telemetry
  const rows = JOINTS.map((j, i) => {
    const lv = s.leader?.positions?.[j];
    const fv = s.follower?.positions?.[j];
    const pct = (v, role) => {
      const [lo, hi] = s[role]?.limits?.[j] || [-180, 180];
      return Math.max(0, Math.min(100, ((v - lo) / (hi - lo || 1)) * 100)).toFixed(1);
    };
    const dots = (typeof lv === 'number' ? `<i style="left:calc(${pct(lv, 'leader')}% - 3px)"></i>` : '') +
                 (typeof fv === 'number' ? `<b style="left:calc(${pct(fv, 'follower')}% - 3px)"></b>` : '');
    return `<div class="joint-row"><div class="joint-name">${LABELS[j]} <span>J${i + 1}</span></div>` +
      `<div class="joint-value">${fmt(lv)}${typeof lv === 'number' ? unit(j) : ''}</div>` +
      `<div class="joint-value follower">${fmt(fv)}${typeof fv === 'number' ? unit(j) : ''}</div>` +
      `<div class="joint-track">${dots}</div></div>`;
  }).join('');
  patch($('#joint-rows'), rows);

  // Activity
  patch($('#activity-log'), (s.events || []).map(e =>
    `<div class="event ${esc(e.level)}"><i></i><p>${esc(e.text)}<time>${clock(e.time)}</time></p></div>`).join(''));
}

function renderSessions(s) {
  const rec = s.recording;
  setText($('#record-title'), rec ? `Recording “${rec.label}”` : 'Start with a demonstration.');
  setText($('#record-description'), rec
    ? `${rec.frames} frames · ${fmt(rec.duration ?? 0)} s so far. Joint data only.`
    : 'Recording saves timestamped joint traces. Camera frames are not included yet.');
  const toggle = $('#record-toggle');
  setText(toggle, rec ? '■ Stop recording' : '● Start recording');
  toggle.disabled = !rec && (!s.connected || s.mode === 'calibrating');
  $('#session-label').disabled = !!rec;

  const list = s.sessions || [];
  setText($('#session-count'), `${list.length} saved`);
  patch($('#session-list'), list.length ? list.map(x => `
    <div class="panel session-card"><span class="session-glyph">∿</span>
      <div><h3>${esc(x.label)}</h3><p>${esc(new Date(x.created).toLocaleString())} · ${esc(x.frames)} frames · ${fmt(x.duration)} s</p></div>
      <a class="button ghost" href="/api/session/${encodeURIComponent(x.id)}" download>Download JSONL ↓</a>
    </div>`).join('') : `
    <div class="empty"><div class="empty-symbol">∿</div><h3>No sessions yet</h3>
      <p>Connect your arms, name a session, and record while you teleoperate.</p></div>`);
}

function renderCameras(s) {
  const cams = s.cameras || {};
  const html = Object.entries(cams).map(([role, c]) => `
    <section class="panel" data-camera="${esc(role)}">
      <div class="camera-view">${c.running && c.has_frame
        ? `<img alt="${esc(c.label)} preview" data-preview="${esc(role)}">`
        : `<div><div class="empty-symbol">▣</div><p>${c.error ? esc(c.error) : c.running ? 'Opening camera…' : 'Preview is off. The camera opens only when you start it.'}</p></div>`}</div>
      <div class="camera-details">
        <h2>${esc(c.label)}</h2>
        <p>Device ${esc(c.index)} · ${esc(c.width)}×${esc(c.height)} · ${esc(c.fps)} fps${c.running ? ' · <span class="state-tag good">LIVE</span>' : ''}</p>
        <div class="camera-controls">
          <button class="button ${c.running ? 'secondary' : 'primary'}" data-camera-toggle="${esc(role)}">${c.running ? '■ Stop preview' : '▶ Start preview'}</button>
          <button class="button ghost" data-camera-edit="${esc(role)}"${c.running ? ' disabled' : ''}>Edit profile</button>
        </div>
      </div>
    </section>`).join('');
  patch($('#camera-cards'), html);
}

function refreshPreviews() {
  if (app.page !== 'cameras') return;
  $$('img[data-preview]').forEach(img => {
    if (img.dataset.loading === '1') return;
    img.dataset.loading = '1';
    const next = new Image();
    next.onload = next.onerror = () => {
      if (next.complete && next.naturalWidth) img.src = next.src;
      img.dataset.loading = '0';
    };
    next.src = `/api/camera/${img.dataset.preview}.jpg?t=${Date.now()}`;
  });
}

function renderSettings(s) {
  const p = s.profile || {};
  renderDeviceSetup(s);
  if (!app.settingsDirty) {
    $('#fps-setting').value = p.fps ?? 40;
    $('#travel-setting').value = p.starter_travel ?? 10;
    syncSliderLabels();
  }
  $('#save-settings').disabled = s.mode === 'teleoperation';

  const row = (k, v) => `<div class="profile-row"><span>${esc(k)}</span><code>${esc(v)}</code></div>`;
  patch($('#profile-details'), [
    row('Profile', p.name),
    ...['leader', 'follower'].map(r => row(`${r[0].toUpperCase() + r.slice(1)} · ${p[r]?.model}`,
      `${p[r]?.id} · ${s[r]?.port || usbStatus(s, r)}`)),
    row('USB devices detected', (s.ports || []).length),
  ].join(''));

  $$('[data-calibrate]').forEach(b => { b.disabled = !s.connected || !!s.calibration || s.mode === 'teleoperation'; });
  $('#disconnect').disabled = !s.connected;

  const wiz = $('#calibration-wizard');
  const c = s.calibration;
  wiz.hidden = !c;
  if (!c) return patch(wiz, '');
  const who = c.role === 'leader' ? 'leader' : 'follower';
  if (c.phase === 'pose') {
    patch(wiz, `<div class="wizard"><h3>Step 1 · Set the ${who}’s middle pose</h3>
      <p>Keep the arm supported. Move every joint to the middle of its travel, matching the reference starting pose, then record it.</p>
      <div class="wizard-actions"><button class="button primary" data-wizard="calibration_pose">Record starting pose</button>
      <button class="button ghost" data-wizard="calibration_cancel">Cancel & restore</button></div></div>`);
  } else {
    const ranges = Object.entries(c.ranges || {}).map(([j, [lo, hi]]) =>
      `<div class="range-row"><span>${LABELS[j] || esc(j)}</span><span>${lo} – ${hi} · ${hi - lo >= 100 ? '✓' : 'move more'}</span></div>`).join('');
    patch(wiz, `<div class="wizard"><h3>Step 2 · Sweep each joint’s range</h3>
      <p>Slowly move each of the five joints below through its full usable range. Wrist twist is skipped and keeps a full turn.</p>
      ${ranges || '<p>Waiting for readings…</p>'}
      <div class="wizard-actions"><button class="button primary" data-wizard="calibration_finish">Save calibration</button>
      <button class="button ghost" data-wizard="calibration_cancel">Cancel & restore</button></div></div>`);
  }
}

function renderDeviceSetup(s) {
  const root = $('#device-setup');
  if (root.contains(document.activeElement)) return;
  const p = s.profile || {};
  const options = role => '<option value="">Select USB controller…</option>' + (s.ports || []).map(d => {
    const selected = p[role]?.serial ? d.serial === p[role].serial : d.device === p[role]?.port;
    return `<option value="${esc(d.device)}"${selected ? ' selected' : ''}>${esc(d.description)} · ${esc(d.serial || d.device)}</option>`;
  }).join('');
  patch(root, `<p class="small">Assign your own controllers before connecting. Unplug one arm at a time if you need to identify them. Keep existing calibration names to reuse saved LeRobot calibration.</p>
    <div class="device-form"><label>Motor family<select name="family" ${s.connected ? 'disabled' : ''}><option value="hiwonder" ${p.family === 'hiwonder' ? 'selected' : ''}>Hiwonder HX-10HM / HX-30HM</option><option value="feetech" ${p.family === 'feetech' ? 'selected' : ''}>Feetech STS3215 · experimental</option></select></label>
    ${['leader','follower'].map(role => `<fieldset ${s.connected ? 'disabled' : ''}><legend>${role[0].toUpperCase()+role.slice(1)}</legend><label>USB controller<select name="${role}-port">${options(role)}</select></label><label>Calibration name<input name="${role}-id" value="${esc(p[role]?.id)}" maxlength="60" pattern="[A-Za-z0-9_-]+"></label></fieldset>`).join('')}</div>
    <button class="button secondary" data-save-profile ${s.connected || app.demo ? 'disabled' : ''}>Save arm assignments</button>
    <p class="small muted">${s.connected ? 'Disconnect USB control to change assignments.' : app.demo ? 'Demo uses simulated devices and never opens USB ports.' : 'Your assignments and recordings stay on this computer.'}</p>`);
}

// ---------- joint speed limits (Operate page, live) ----------
const speedUnit = j => (j === 'gripper' ? '%/s' : '°/s');

function buildSpeedPanel() {
  const {defaults, max, min} = app.speeds;
  $('#speed-grid').innerHTML = JOINTS.map((j, i) => `
    <label class="speed-tile" data-speed-tile="${j}"><span class="speed-name">${LABELS[j]}<i>J${i + 1}</i></span>
      <b><span data-speed-value>${defaults[j]}</span><small>${speedUnit(j)}</small></b>
      <input type="range" data-speed="${j}" min="${min}" max="${max[j]}" step="5" value="${defaults[j]}" aria-label="${LABELS[j]} speed limit"></label>`).join('');
  $$('#speed-grid input').forEach(input => {
    const j = input.dataset.speed;
    input.addEventListener('input', () => {
      app.speedHold[j] = Date.now() + 1500;
      setText($(`[data-speed-tile="${j}"] [data-speed-value]`), input.value);
    });
    input.addEventListener('change', () => {
      app.speedHold[j] = Date.now() + 1500;
      act('settings', {joint_speeds: {[j]: +input.value}});
    });
  });
}

function renderSpeeds(s, starterCap) {
  if (!app.speeds) return;
  const current = s.profile?.joint_speeds || {};
  for (const j of JOINTS) {
    const input = $(`[data-speed="${j}"]`);
    if (!input || current[j] === undefined) continue;
    if (Date.now() > (app.speedHold[j] || 0) && +input.value !== current[j]) {
      input.value = current[j];
      setText($(`[data-speed-tile="${j}"] [data-speed-value]`), String(current[j]));
    }
    toggleClass($(`[data-speed-tile="${j}"]`), 'custom', current[j] !== app.speeds.defaults[j]);
  }
  const cap = $('#speed-cap');
  setText(cap, starterCap ? 'STARTER CAP · 10°/S' : 'APPLIES LIVE');
  cap.className = 'state-tag' + (starterCap ? ' warn' : ' good');
  $('#speed-reset').disabled = JOINTS.every(j => current[j] === app.speeds.defaults[j]);
}

function syncSliderLabels() {
  setText($('#fps-value'), `${$('#fps-setting').value} Hz`);
  setText($('#travel-value'), `${$('#travel-setting').value}° each way`);
}

// Page registry: new modules (datasets, training, evaluation) add an entry here and a section in index.html.
const PAGES = {operate: renderOperate, sessions: renderSessions, cameras: renderCameras, learning: () => {}, settings: renderSettings};

function render() {
  const s = app.state;
  if (!s) return;
  renderChrome(s);
  (PAGES[app.page] || (() => {}))(s);
}

// ---------- routing ----------
function route() {
  const page = location.hash.slice(1) in PAGES ? location.hash.slice(1) : 'operate';
  app.page = page;
  $$('.page').forEach(el => { el.hidden = el.id !== 'page-' + page; });
  $$('nav a[data-page]').forEach(a => toggleClass(a, 'active', a.dataset.page === page));
  setText($('#page-label'), PAGE_NAMES[page]);
  render();
  window.scrollTo(0, 0);
}

// ---------- polling ----------
let pollTimer;
async function poll() {
  clearTimeout(pollTimer);
  try {
    app.state = await api('/api/state');
    app.failures = 0;
    if (app.online !== true) {
      app.online = true;
      if ($('#error-banner').dataset.offline) { $('#error-banner').hidden = true; $('#error-banner').dataset.offline = ''; }
    }
    render();
  } catch {
    app.failures += 1;
    if (app.failures >= 3 && app.online !== false) {
      app.online = false;
      const banner = $('#error-banner');
      banner.textContent = 'Cannot reach the local control service. Restart it with “Arm Studio.command”. The follower keeps holding if it was holding.';
      banner.dataset.offline = '1';
      banner.hidden = false;
      setText($('#footer-status'), 'Local control service offline');
      toggleClass($('#connection'), 'online', false);
    }
  }
  const fast = app.state?.mode === 'teleoperation' || app.state?.calibration;
  pollTimer = setTimeout(poll, fast ? 150 : 400);
}

// ---------- actions ----------
function bind() {
  window.addEventListener('hashchange', route);
  $('#modal-close').addEventListener('click', () => $('#modal').close());

  $('#error-banner').addEventListener('click', e => {
    if (e.currentTarget.dataset.offline) return;
    app.dismissedError = app.state?.error || null;
    e.currentTarget.hidden = true;
  });

  const stop = () => act('stop', {}, 'Stopped. The follower keeps holding.');
  $('#global-stop').addEventListener('click', stop);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') stop(); });

  $('#connect').addEventListener('click', async () => {
    if (!app.state?.connected && !['leader','follower'].every(r => app.state?.profile?.[r]?.serial || app.state?.profile?.[r]?.port)) {
      location.hash = 'settings';
      return toast('Select the leader and follower USB controllers first.');
    }
    if (!app.state?.connected) return act('connect', {}, 'Connecting both arms…');
    const ok = await modal({title: 'Disconnect USB control?', confirm: 'Disconnect',
      html: '<p>Following stops and the dashboard lets go of both USB ports. Motor power is not removed. The follower stays holding if it is holding now.</p>'});
    if (ok) act('disconnect', {}, 'Disconnected.');
  });
  $('#disconnect').addEventListener('click', () => $('#connect').click());

  $$('#control-mode button').forEach(b => b.addEventListener('click', () => { app.control = b.dataset.control; render(); }));

  $('#start').addEventListener('click', () => {
    if (app.state?.mode === 'teleoperation') return stop();
    act('start', {control: app.control}, app.control === 'full' ? 'Full teleoperation starting. The follower eases into the leader’s pose.' : 'Starter teleoperation starting.');
  });

  $('#hold').addEventListener('click', async () => {
    const ok = await modal({eyebrow: 'FOLLOWER', title: 'Enable holding', confirm: 'Hold this pose',
      html: '<p>Support the follower’s forearm in a comfortable pose. It locks where it is when you confirm; then ease your hand away.</p>',
      ack: 'I am supporting the follower.'});
    if (ok) act('hold', {supported: true}, 'Follower holding.');
  });

  $('#release').addEventListener('click', async () => {
    const ok = await modal({eyebrow: 'FOLLOWER', title: 'Release the follower?', confirm: 'Release motors',
      html: '<p>The follower’s motors go limp and it will fall under its own weight unless supported or resting.</p>',
      ack: 'I am supporting the follower, or it is resting safely.'});
    if (ok) act('release', {supported: true}, 'Follower released.');
  });

  // Sessions
  $('#record-toggle').addEventListener('click', () => {
    if (app.state?.recording) return act('record_stop', {}, 'Recording saved.');
    act('record_start', {label: $('#session-label').value}, 'Recording started.');
    $('#session-label').value = '';
  });

  // Cameras (delegated: cards re-render)
  $('#camera-cards').addEventListener('click', async e => {
    const toggle = e.target.closest('[data-camera-toggle]');
    const edit = e.target.closest('[data-camera-edit]');
    if (toggle) {
      const role = toggle.dataset.cameraToggle;
      const running = app.state?.cameras?.[role]?.running;
      return act(running ? 'camera_stop' : 'camera_start', {role}, running ? 'Preview stopped.' : 'Opening camera…');
    }
    if (edit && !edit.disabled) {
      const role = edit.dataset.cameraEdit;
      const c = app.state.cameras[role];
      const field = (k, label, min, max) => `<label>${label}<input type="number" name="${k}" value="${esc(c[k])}" min="${min}" max="${max}" required></label>`;
      const values = await modal({eyebrow: 'CAMERA PROFILE', title: c.label, confirm: 'Save profile',
        html: `<p>Camera indices can change after reconnecting USB devices. Preview to confirm the view.</p><div class="camera-form">
          ${field('index', 'Device index', 0, 9)}${field('fps', 'Frames per second', 1, 30)}
          ${field('width', 'Width', 160, 1920)}${field('height', 'Height', 120, 1080)}</div>`,
        read: dialog => Object.fromEntries($$('.camera-form input', dialog).map(i => {
          if (!i.checkValidity()) throw new Error(`${i.closest('label').firstChild.textContent.trim()} is out of range.`);
          return [i.name, Number(i.value)];
        }))});
      if (values) act('camera_save', {role, ...values}, 'Camera profile saved.');
    }
  });

  // Settings
  $('#device-setup').addEventListener('click', e => {
    if (!e.target.closest('[data-save-profile]')) return;
    const root = $('#device-setup');
    const values = {family: $('[name="family"]', root).value};
    for (const role of ['leader','follower']) values[role] = {
      port: $(`[name="${role}-port"]`, root).value,
      id: $(`[name="${role}-id"]`, root).value,
    };
    act('profile', values, 'Arm assignments saved.');
  });
  ['#fps-setting', '#travel-setting'].forEach(id => $(id).addEventListener('input', () => {
    app.settingsDirty = true;
    syncSliderLabels();
  }));
  $('#save-settings').addEventListener('click', async () => {
    await act('settings', {fps: +$('#fps-setting').value, starter_travel: +$('#travel-setting').value}, 'Preferences saved.');
    app.settingsDirty = false;
  });

  $('#speed-reset').addEventListener('click', () => {
    JOINTS.forEach(j => { app.speedHold[j] = 0; });
    act('settings', {joint_speeds: app.speeds.defaults}, 'Speed limits reset to defaults.');
  });

  $$('[data-calibrate]').forEach(b => b.addEventListener('click', async () => {
    const role = b.dataset.calibrate;
    const ok = await modal({eyebrow: 'GUIDED CALIBRATION', title: `Recalibrate the ${role}?`, confirm: 'Release & begin',
      html: `<p>The ${role}’s motors are released for calibration, and the saved calibration is replaced when you finish. Cancelling restores the current one.</p>` +
        (role === 'follower' ? '<p>The follower will go limp immediately.</p>' : ''),
      ack: `I am supporting the ${role}.`});
    if (ok) act('calibrate', {role, supported: true}, `${role[0].toUpperCase() + role.slice(1)} released for calibration.`);
  }));
  $('#calibration-wizard').addEventListener('click', e => {
    const b = e.target.closest('[data-wizard]');
    if (b) act(b.dataset.wizard, {});
  });
}

// ---------- start ----------
async function boot() {
  bind();
  route();
  try {
    const boot = await api('/api/bootstrap');
    app.token = boot.token;
    app.modules = boot.modules;
    app.demo = boot.demo;
    app.speeds = boot.speeds;
    buildSpeedPanel();
    if (app.demo) {
      setText($('.local-label'), 'DEMO · SIMULATED ARMS');
      setText($('.activity-footer'), 'Demo mode: simulated arms, no USB access.');
    }
    for (const m of app.modules) {
      const link = $(`nav a[data-page="${m.id}"]`);
      if (link) link.title = m.description;
    }
  } catch (err) {
    showBanner('Could not start the dashboard session: ' + err.message);
  }
  poll();
  setInterval(() => { if (app.token) api('/api/heartbeat', {}).catch(() => {}); }, 2000);
  setInterval(refreshPreviews, 150);
}

boot();
