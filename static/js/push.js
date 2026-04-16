// VPSMon — PWA + Web Push client
(function () {
  'use strict';

  const PushMod = {
    supported: 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window,
    reg: null,
    vapidPublicKey: null,
  };

  // --- Register service worker ---
  PushMod.registerSW = async function () {
    if (!('serviceWorker' in navigator)) return null;
    try {
      const reg = await navigator.serviceWorker.register('/static/sw.js', { scope: '/' });
      PushMod.reg = reg;
      navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data && e.data.type === 'notification-click') {
          location.hash = '#alerts';
        }
      });
      // auto-update: if a new sw is waiting, reload when activated
      if (reg.waiting) reg.waiting.postMessage({ type: 'skip-waiting' });
      reg.addEventListener('updatefound', () => {
        const nw = reg.installing;
        if (!nw) return;
        nw.addEventListener('statechange', () => {
          if (nw.state === 'installed' && navigator.serviceWorker.controller) {
            if (window.VPSMon && VPSMon.toast) VPSMon.toast('New version available — refresh to update', 'info', 8000);
          }
        });
      });
      return reg;
    } catch (e) {
      console.warn('SW register failed', e);
      return null;
    }
  };

  // --- Fetch VAPID public key ---
  async function getVapidKey() {
    if (PushMod.vapidPublicKey) return PushMod.vapidPublicKey;
    try {
      const r = await fetch('/api/push/vapid-public-key');
      if (!r.ok) return null;
      const d = await r.json();
      PushMod.vapidPublicKey = d.key;
      return d.key;
    } catch { return null; }
  }

  function urlB64ToUint8(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const s = (b64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(s);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  // --- Subscribe ---
  PushMod.subscribe = async function () {
    if (!PushMod.supported) throw new Error('Push not supported in this browser');
    const reg = PushMod.reg || (await navigator.serviceWorker.ready);
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') throw new Error('Notification permission denied');
    const key = await getVapidKey();
    if (!key) throw new Error('Server missing VAPID key');
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8(key),
      });
    }
    const resp = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: sub.toJSON().keys,
        ua: navigator.userAgent,
      }),
    });
    if (!resp.ok) throw new Error('Subscribe failed');
    return sub;
  };

  // --- Unsubscribe ---
  PushMod.unsubscribe = async function () {
    const reg = PushMod.reg || (await navigator.serviceWorker.ready);
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await fetch('/api/push/unsubscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
      await sub.unsubscribe();
    }
    return true;
  };

  PushMod.isSubscribed = async function () {
    if (!PushMod.supported) return false;
    try {
      const reg = PushMod.reg || (await navigator.serviceWorker.ready);
      const sub = await reg.pushManager.getSubscription();
      return !!sub;
    } catch { return false; }
  };

  PushMod.testPush = async function () {
    const r = await fetch('/api/push/test', { method: 'POST' });
    return r.ok;
  };

  // --- Install prompt ---
  let deferredInstall = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstall = e;
    const btn = document.getElementById('install-app-btn');
    if (btn) btn.style.display = 'inline-flex';
  });

  PushMod.showInstall = async function () {
    if (!deferredInstall) {
      if (window.VPSMon && VPSMon.toast) VPSMon.toast('App is already installed or install not supported', 'info');
      return;
    }
    deferredInstall.prompt();
    const choice = await deferredInstall.userChoice;
    deferredInstall = null;
    const btn = document.getElementById('install-app-btn');
    if (btn) btn.style.display = 'none';
    return choice.outcome;
  };

  window.VPSMonPush = PushMod;

  // Auto-register SW on load
  if (document.readyState === 'complete') PushMod.registerSW();
  else window.addEventListener('load', () => PushMod.registerSW());
})();
