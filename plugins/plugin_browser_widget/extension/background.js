/*
 * Background service worker (Chrome/Edge MV3).
 * - Adds a context-menu "Позвонить через АТС" for selected numbers.
 * - Opens the phone popup and passes the number to dial.
 * - Toggles the injected floating panel on the active tab.
 */

const PANEL_HTML = 'widget.html';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'bw-call-selection',
    title: 'Позвонить через АТС: "%s"',
    contexts: ['selection']
  });
  chrome.contextMenus.create({
    id: 'bw-call-click2call',
    title: 'Позвонить через АТС (click-to-call)',
    contexts: ['selection']
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const number = (info.selectionText || '').replace(/[^\d+]/g, '');
  if (!number) return;
  // Open the popup window prefilled — simplest cross-browser flow.
  chrome.windows.create({
    url: chrome.runtime.getURL(PANEL_HTML) + '?dial=' + encodeURIComponent(number) +
         (info.menuItemId === 'bw-call-click2call' ? '&mode=c2c' : '&mode=webrtc'),
    type: 'popup',
    width: 380,
    height: 560
  });
});

chrome.action.onClicked.addListener((tab) => {
  chrome.windows.create({
    url: chrome.runtime.getURL(PANEL_HTML),
    type: 'popup',
    width: 380,
    height: 560
  });
});

// A click on a phone number detected by the content script opens the widget.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'bw-dial' && msg.number) {
    chrome.windows.create({
      url: chrome.runtime.getURL(PANEL_HTML) + '?dial=' + encodeURIComponent(msg.number) + '&mode=webrtc',
      type: 'popup',
      width: 380,
      height: 560
    });
  }
});
