/*
 * Content script: detect phone numbers on the page and turn them into
 * click-to-call links, plus provide a page-level bridge to the widget.
 *
 * It does not phone home by itself; clicking a number sends a message to the
 * background/popup context. We keep it conservative: only text nodes with a
 * clear phone pattern are linked.
 */

(function () {
  const PHONE_RE = /(?:\+?\d[\d\s\-().]{6,}\d)/g;
  const MIN_DIGITS = 7;

  function normalize(n) {
    return String(n || '').replace(/[^\d+*#]/g, '');
  }

  function isPhone(text) {
    const digits = (text.match(/\d/g) || []).length;
    return digits >= MIN_DIGITS && digits <= 15 && /^[\d\s\-().+]+$/.test(text.trim());
  }

  function processNode(node) {
    if (!node || !node.nodeValue) return;
    if (!/\d{3,}/.test(node.nodeValue)) return;
    const matches = node.nodeValue.match(PHONE_RE);
    if (!matches) return;
    let hasMatch = false;
    for (const m of matches) { if (isPhone(m)) { hasMatch = true; break; } }
    if (!hasMatch) return;

    const frag = document.createDocumentFragment();
    let last = 0;
    node.nodeValue.replace(PHONE_RE, (m, offset) => {
      if (!isPhone(m)) return m;
      if (offset > last) frag.appendChild(document.createTextNode(node.nodeValue.slice(last, offset)));
      const a = document.createElement('a');
      a.href = '#';
      a.textContent = m;
      a.className = 'bw-phone-link';
      a.style.cssText = 'color:inherit;border-bottom:1px dashed #06b6d4;cursor:pointer;text-decoration:none;';
      a.title = 'Позвонить через АТС: ' + m;
      a.addEventListener('click', (ev) => {
        ev.preventDefault();
        dial(normalize(m));
      });
      frag.appendChild(a);
      last = offset + m.length;
      return m;
    });
    if (last < node.nodeValue.length) {
      frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
    }
    try {
      node.parentNode.replaceChild(frag, node);
    } catch (e) { /* node may have changed */ }
  }

  function walk(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.parentNode) return NodeFilter.FILTER_REJECT;
        const tag = n.parentNode.nodeName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'A' ||
            tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'NOSCRIPT') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(processNode);
  }

  function dial(number) {
    const loader = typeof chrome !== 'undefined' ? chrome : (typeof browser !== 'undefined' ? browser : null);
    if (loader && loader.runtime && loader.runtime.sendMessage) {
      loader.runtime.sendMessage({ type: 'bw-dial', number });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => walk(document.body));
  } else {
    walk(document.body);
  }

  // Observe dynamic CRM pages (amoCRM/Bitrix render asynchronously)
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType === Node.TEXT_NODE) processNode(node);
        else if (node.nodeType === Node.ELEMENT_NODE) walk(node);
      }
    }
  });
  try {
    obs.observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* ignore */ }
})();
