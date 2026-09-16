/*
 * Browser Softphone Widget — core logic.
 *
 * Modes:
 *   - WebRTC: places/receives calls directly in the browser via SIP-over-WSS.
 *   - Click-to-call: asks the PBX panel to ring the operator's extension and
 *     then dial the target number (works even without WebRTC support).
 *
 * This module is shared between the extension popup and the injected
 * on-page panel. It talks to the panel REST API using an X-API-Key.
 */

const DEFAULT_SETTINGS = {
  server: '',        // e.g. 138.124.229.10:8888
  apiKey: '',        // X-API-Key from the panel
  exten: '',         // operator extension
  password: '',      // SIP password for WebRTC
  wsUrl: ''          // optional explicit wss:// url
};

const SC = {
  idle: 'off',
  registering: 'off',
  registered: 'on',
  call: 'on',
  error: 'err'
};

function apiBase() {
  let s = (state.settings.server || '').trim().replace(/\/+$/, '');
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
  return s;
}

function wsUrl() {
  if (state.settings.wsUrl && state.settings.wsUrl.trim()) return state.settings.wsUrl.trim();
  const base = apiBase();
  if (!base) return '';
  try {
    const u = new URL(base);
    return 'wss://' + u.hostname + '/ws';
  } catch (e) {
    return '';
  }
}

function normalizeNumber(n) {
  return String(n || '').replace(/[^\d+*#]/g, '');
}

const state = {
  settings: { ...DEFAULT_SETTINGS },
  ua: null,
  session: null,
  registered: false,
  muted: false,
  timer: null,
  startedAt: 0,
  poll: null
};

/* ---------- storage (extension or localStorage) ---------- */
async function loadSettings() {
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
    const data = await chrome.storage.local.get(['bw_settings']);
    state.settings = { ...DEFAULT_SETTINGS, ...(data.bw_settings || {}) };
  } else {
    try {
      state.settings = { ...DEFAULT_SETTINGS, ...(JSON.parse(localStorage.getItem('bw_settings') || '{}')) };
    } catch (e) { /* ignore */ }
  }
  return state.settings;
}

async function saveSettings(settings) {
  state.settings = { ...DEFAULT_SETTINGS, ...settings };
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
    await chrome.storage.local.set({ bw_settings: state.settings });
  } else {
    localStorage.setItem('bw_settings', JSON.stringify(state.settings));
  }
}

/* ---------- UI helpers ---------- */
const $ = (id) => document.getElementById(id);

function log(msg) {
  const el = $('w-log');
  if (!el) return;
  const line = document.createElement('div');
  line.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function setStatus(text, kind) {
  const el = $('w-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'w-status ' + (kind || 'off');
}

function startTimer() {
  state.startedAt = Date.now();
  stopTimer();
  state.timer = setInterval(() => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    const el = $('w-timer');
    if (el) el.textContent = mm + ':' + ss;
  }, 500);
}

function stopTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
}

function showCall(active) {
  $('w-call').classList.toggle('hidden', !active);
  $('w-dialer').classList.toggle('hidden', !!active);
}

/* ---------- panel API ---------- */
async function api(path, options = {}) {
  const base = apiBase();
  if (!base) throw new Error('Не задан адрес АТС');
  const headers = Object.assign(
    { 'Content-Type': 'application/json' },
    options.headers || {}
  );
  if (state.settings.apiKey) headers['X-API-Key'] = state.settings.apiKey;
  const res = await fetch(base + path, { ...options, headers });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || ('HTTP ' + res.status);
    throw new Error(msg);
  }
  return data;
}

async function clickToCall(target) {
  const phone = normalizeNumber(target);
  if (!phone) throw new Error('Введите номер');
  log('click-to-call → ' + phone);
  const data = await api('/api/widget/call', {
    method: 'POST',
    body: JSON.stringify({ operator: state.settings.exten, phone })
  });
  if (!data || data.status !== 'success') {
    throw new Error((data && (data.error || data.message)) || 'Ошибка вызова');
  }
  log('Вызов инициирован: ' + (data.message || 'ок'));
  return data;
}

async function autofillFromPanel() {
  const data = await api('/api/widget/config');
  if (data && data.status === 'ok') {
    const s = state.settings;
    if (data.exten) s.exten = data.exten;
    if (data.password) s.password = data.password;
    if (data.ws_url) s.wsUrl = data.ws_url;
    if (data.server) s.server = data.server;
    await saveSettings(s);
    fillSettingsForm();
    log('Настройки получены с АТС');
  }
  return data;
}

/* ---------- WebRTC (sip.js) ---------- */
let SIP = null;
async function ensureSip() {
  if (SIP) return SIP;
  if (typeof window !== 'undefined' && window.__BW_SIP) return (SIP = window.__BW_SIP);
  let mod;
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL) {
    mod = await import(chrome.runtime.getURL('sip.js'));
  } else {
    mod = await import('./sip.js');
  }
  // sip.js shim default-exports the module namespace
  SIP = mod.default || mod;
  if (SIP.__error) throw new Error(SIP.__error);
  return SIP;
}

async function register() {
  if (state.registered) { unregister(); return; }
  const s = state.settings;
  if (!s.exten || !s.password) { log('Заполните номер и SIP-пароль'); return; }
  const wss = wsUrl();
  if (!wss) { log('Не задан адрес АТС'); return; }
  try {
    setStatus('подключение…', 'off');
    const lib = await ensureSip();
    let host = '';
    try { host = new URL(wss).hostname; } catch (e) {}
    state.ua = new lib.UserAgent({
      uri: lib.UserAgent.makeURI('sip:' + s.exten + '@' + host),
      transportOptions: { server: wss },
      authorizationUsername: s.exten,
      authorizationPassword: s.password,
      delegate: {
        onConnect: () => {
          state.registered = true;
          setStatus('подключён', 'on');
          log('WebRTC подключён');
          const b = $('w-register'); if (b) b.textContent = 'Отключить WebRTC';
        },
        onDisconnect: () => {
          state.registered = false;
          setStatus('не подключён', 'off');
          const b = $('w-register'); if (b) b.textContent = 'Подключить WebRTC';
        },
        onInvite: (invitation) => handleIncoming(invitation)
      }
    });
    await state.ua.start();
  } catch (e) {
    setStatus('ошибка', 'err');
    log('Ошибка WebRTC: ' + e.message);
  }
}

function unregister() {
  try { if (state.ua) state.ua.stop(); } catch (e) {}
  state.ua = null;
  state.registered = false;
  setStatus('не подключён', 'off');
  const b = $('w-register'); if (b) b.textContent = 'Подключить WebRTC';
}

async function callWebRTC(target) {
  const s = state.settings;
  if (!state.ua || !state.registered) { log('Сначала подключите WebRTC'); return; }
  const lib = await ensureSip();
  const dest = normalizeNumber(target);
  if (!dest) { log('Введите номер'); return; }
  try {
    const host = new URL(wsUrl()).hostname;
    const uri = lib.UserAgent.makeURI('sip:' + dest + '@' + host);
    state.session = new lib.Inviter(state.ua, uri);
    setupSession(state.session, dest);
    await state.session.invite();
    showCall(true);
    $('w-peer-num').textContent = dest;
    startTimer();
    setStatus('звонок', 'on');
  } catch (e) {
    log('Ошибка вызова: ' + e.message);
  }
}

function handleIncoming(invitation) {
  state.session = invitation;
  const from = (invitation.remoteIdentity && invitation.remoteIdentity.uri && invitation.remoteIdentity.uri.user) || 'входящий';
  $('w-peer-num').textContent = from;
  showCall(true);
  showCall(true);
  $('w-answer').classList.remove('hidden');
  setStatus('входящий', 'on');
  window.__bwIncoming = invitation;
  setupSession(invitation, from);
}

function setupSession(session, peer) {
  const lib = SIP;
  session.stateChange.addListener((st) => {
    if (st === lib.SessionState.Established) {
      attachMedia(session);
      $('w-answer').classList.add('hidden');
      startTimer();
      setStatus('разговор', 'on');
    } else if (st === lib.SessionState.Terminated) {
      cleanupSession();
    }
  });
}

function attachMedia(session) {
  const pc = session.sessionDescriptionHandler && session.sessionDescriptionHandler.peerConnection;
  if (!pc) return;
  const remote = document.createElement('audio');
  remote.autoplay = true;
  remote.id = 'bw-remote-audio';
  document.body.appendChild(remote);
  const stream = new MediaStream();
  pc.getReceivers().forEach((r) => { if (r.track) stream.addTrack(r.track); });
  remote.srcObject = stream;
  remote.play().catch(() => {});
}

async function answer() {
  if (!state.session) return;
  try {
    await state.session.accept();
    $('w-answer').classList.add('hidden');
  } catch (e) { log('Ошибка ответа: ' + e.message); }
}

async function hangup() {
  const s = state.session;
  if (!s) { cleanupSession(); return; }
  try {
    const lib = SIP;
    switch (s.state) {
      case lib.SessionState.Initial:
      case lib.SessionState.Establishing:
        if (s instanceof lib.Inviter) await s.cancel(); else await s.reject();
        break;
      case lib.SessionState.Established:
        await s.bye();
        break;
    }
  } catch (e) { /* ignore */ }
  cleanupSession();
}

function cleanupSession() {
  stopTimer();
  state.session = null;
  const remote = $('bw-remote-audio');
  if (remote) remote.remove();
  showCall(false);
  $('w-answer').classList.add('hidden');
  if (state.registered) setStatus('подключён', 'on'); else setStatus('не подключён', 'off');
}

function toggleMute() {
  if (!state.session) return;
  const pc = state.session.sessionDescriptionHandler && state.session.sessionDescriptionHandler.peerConnection;
  if (!pc) return;
  state.muted = !state.muted;
  pc.getSenders().forEach((sn) => { if (sn.track) sn.track.enabled = !state.muted; });
  const b = $('w-mute'); if (b) b.textContent = state.muted ? 'Unmute' : 'Mute';
}

function sendDtmf(digit) {
  if (state.session && state.session.sendDTMF) state.session.sendDTMF(digit);
}

function buildDtmfPad() {
  const pad = $('w-dtmf');
  if (!pad || pad.dataset.built) return;
  pad.dataset.built = '1';
  ['1','2','3','4','5','6','7','8','9','*','0','#'].forEach((d) => {
    const b = document.createElement('button');
    b.className = 'w-btn small';
    b.textContent = d;
    b.onclick = () => sendDtmf(d);
    pad.appendChild(b);
  });
}

/* ---------- page number detection ---------- */
function findNumberOnPage() {
  const sel = window.getSelection && window.getSelection();
  let text = sel && sel.toString ? sel.toString() : '';
  if (!text) {
    text = document.body ? document.body.innerText : '';
  }
  const matches = text.match(/(?:\+?\d[\d\s\-().]{6,}\d)/g) || [];
  const cleaned = matches.map(normalizeNumber).filter((n) => n.replace(/\D/g, '').length >= 7);
  return cleaned.length ? cleaned[cleaned.length - 1] : '';
}

/* ---------- settings form ---------- */
function fillSettingsForm() {
  const s = state.settings;
  if ($('s-server')) $('s-server').value = s.server || '';
  if ($('s-apikey')) $('s-apikey').value = s.apiKey || '';
  if ($('s-exten')) $('s-exten').value = s.exten || '';
  if ($('s-password')) $('s-password').value = s.password || '';
  if ($('s-ws')) $('s-ws').value = s.wsUrl || '';
}

function readSettingsForm() {
  return {
    server: ($('s-server') && $('s-server').value.trim()) || '',
    apiKey: ($('s-apikey') && $('s-apikey').value.trim()) || '',
    exten: ($('s-exten') && $('s-exten').value.trim()) || '',
    password: ($('s-password') && $('s-password').value.trim()) || '',
    wsUrl: ($('s-ws') && $('s-ws').value.trim()) || ''
  };
}

/* ---------- boot ---------- */
export async function boot() {
  await loadSettings();
  fillSettingsForm();
  buildDtmfPad();
  wireEvents();
  log('Виджет готов. Заполните настройки (⚙).');

  // Prefill from URL query (?dial=...&mode=webrtc|c2c)
  try {
    const q = new URLSearchParams(location.search);
    const dial = q.get('dial');
    if (dial) {
      $('w-target').value = normalizeNumber(dial);
      const mode = q.get('mode') || 'webrtc';
      log('Автозвонок: ' + dial + ' (' + mode + ')');
      $('w-settings').classList.add('hidden');
      if (mode === 'c2c') {
        setTimeout(() => clickToCall(dial).catch((e) => log(e.message)), 300);
      } else if (state.registered) {
        setTimeout(() => callWebRTC(dial), 300);
      }
    }
  } catch (e) { /* ignore */ }
}

function wireEvents() {
  const on = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
  on('w-settings-toggle', () => { $('w-settings').classList.toggle('hidden'); });
  on('w-min', () => { document.getElementById('app').classList.toggle('min'); });
  on('w-register', register);
  on('w-call-webrtc', () => callWebRTC($('w-target').value));
  on('w-call-c2c', () => clickToCall($('w-target').value).catch((e) => log(e.message)));
  on('w-answer', answer);
  on('w-hangup', hangup);
  on('w-mute', toggleMute);
  on('w-dtmf', () => $('w-dtmf').classList.toggle('hidden'));
  on('w-lookup', () => {
    const n = findNumberOnPage();
    if (n) { $('w-target').value = n; log('Найден номер: ' + n); }
    else log('Номер на странице не найден');
  });
  on('s-save', async () => {
    await saveSettings(readSettingsForm());
    log('Настройки сохранены');
    $('s-hint').textContent = 'Сохранено';
  });
  on('s-autofill', () => autofillFromPanel().catch((e) => { $('s-hint').textContent = e.message; }));
}

/* Allow use as a plain content script too */
if (typeof window !== 'undefined') {
  window.__bwWidget = { boot, callWebRTC, clickToCall, findNumberOnPage, state };
}
