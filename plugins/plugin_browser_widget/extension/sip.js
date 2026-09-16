// Loads sip.js (ESM bundle) and re-exports it.
// Kept as an import target so widget.js can `import` it uniformly.
const CDN = 'https://cdn.jsdelivr.net/npm/sip.js@0.21.2/+esm';
let mod;
try {
  mod = await import(/* webpackIgnore: true */ CDN);
} catch (e) {
  // Offline / CSP fallback: expose a clear error object
  mod = { __error: 'sip.js не загружен: ' + (e && e.message ? e.message : e) };
}
export default mod;
export const UserAgent = mod.UserAgent;
export const Inviter = mod.Inviter;
export const SessionState = mod.SessionState;
export const RegistererState = mod.RegistererState;
