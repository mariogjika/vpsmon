// VPSMon Command Palette — Cmd-K / Ctrl-K quick actions
(function () {
  'use strict';

  const COMMANDS = [
    // Navigation
    { name: 'Go to Dashboard',        icon: '📊', category: 'Navigation', action: () => VPSMon.goPage('overview'), keys: 'overview home' },
    { name: 'Go to System',           icon: '💻', category: 'Navigation', action: () => VPSMon.goPage('system') },
    { name: 'Go to Docker',           icon: '🐳', category: 'Navigation', action: () => VPSMon.goPage('docker'), keys: 'containers' },
    { name: 'Go to Processes',        icon: '⚙️', category: 'Navigation', action: () => VPSMon.goPage('processes'), keys: 'ps top' },
    { name: 'Go to Logs',             icon: '📜', category: 'Navigation', action: () => VPSMon.goPage('logs'), keys: 'journalctl syslog' },
    { name: 'Go to Security',         icon: '🔒', category: 'Navigation', action: () => VPSMon.goPage('security'), keys: 'ssh auth' },
    { name: 'Go to Network',          icon: '🌐', category: 'Navigation', action: () => VPSMon.goPage('network'), keys: 'connections ports' },
    { name: 'Go to Alerts',           icon: '🚨', category: 'Navigation', action: () => VPSMon.goPage('alerts'), keys: 'notifications' },
    { name: 'Go to Servers',          icon: '🖥️', category: 'Navigation', action: () => VPSMon.goPage('servers'), keys: 'fleet multi-server' },
    { name: 'Go to Uptime Checks',    icon: '📡', category: 'Navigation', action: () => VPSMon.goPage('uptime'), keys: 'synthetic http' },
    { name: 'Go to Incidents',        icon: '📋', category: 'Navigation', action: () => VPSMon.goPage('incidents'), keys: 'timeline postmortem' },
    { name: 'Go to Notifications',    icon: '📧', category: 'Navigation', action: () => VPSMon.goPage('notifications'), keys: 'slack email telegram' },
    { name: 'Go to Audit Log',        icon: '📒', category: 'Navigation', action: () => VPSMon.goPage('settings'), keys: 'audit' },
    { name: 'Go to Runbooks',         icon: '▶️', category: 'Navigation', action: () => VPSMon.goPage('runbooks'), keys: 'scripts automation' },
    { name: 'Go to Updates',          icon: '⬆️', category: 'Navigation', action: () => VPSMon.goPage('updates'), keys: 'apt security packages' },
    { name: 'Go to Backups',          icon: '💾', category: 'Navigation', action: () => VPSMon.goPage('backups'), keys: 'restic borg' },
    { name: 'Go to SSH Keys',         icon: '🔑', category: 'Navigation', action: () => VPSMon.goPage('servers'), keys: 'authorized keys' },
    { name: 'Go to Custom Metrics',   icon: '📏', category: 'Navigation', action: () => VPSMon.goPage('system'), keys: 'probes' },
    { name: 'Go to Settings',         icon: '⚙️', category: 'Navigation', action: () => VPSMon.goPage('settings'), keys: 'preferences' },

    // Actions
    { name: 'Add Server',             icon: '➕', category: 'Actions', action: () => VPSMon.showAddServer && VPSMon.showAddServer(), keys: 'new vps' },
    { name: 'Add Uptime Check',       icon: '➕', category: 'Actions', action: () => VPSMon.showAddUptime && VPSMon.showAddUptime(), keys: 'monitor url' },
    { name: 'Add Notification Channel', icon: '➕', category: 'Actions', action: () => VPSMon.showAddNotif && VPSMon.showAddNotif(), keys: 'slack webhook' },
    { name: 'Enable Push Notifications', icon: '🔔', category: 'Actions', action: () => VPSMon.pushSubscribe && VPSMon.pushSubscribe() },
    { name: 'Send Test Push',         icon: '🔔', category: 'Actions', action: () => VPSMon.pushTest && VPSMon.pushTest() },
    { name: 'Install VPSMon App (PWA)', icon: '📱', category: 'Actions', action: () => VPSMon.installApp && VPSMon.installApp() },
    { name: 'Acknowledge All Alerts', icon: '✓',  category: 'Actions', action: () => VPSMon.ackAllAlerts && VPSMon.ackAllAlerts() },
    { name: 'Create API Token',       icon: '🔑', category: 'Actions', action: () => VPSMon.createToken && VPSMon.createToken(), keys: 'auth api' },
    { name: 'Create New Runbook',     icon: '➕', category: 'Actions', action: () => VPSMon.showAddRunbook && VPSMon.showAddRunbook() },
    { name: 'Scan Package Updates',   icon: '🔎', category: 'Actions', action: () => VPSMon.scanUpdates && VPSMon.scanUpdates() },
    { name: 'Scan Backups',           icon: '🔎', category: 'Actions', action: () => VPSMon.scanBackups && VPSMon.scanBackups() },
    { name: 'Scan SSH Keys',          icon: '🔎', category: 'Actions', action: () => VPSMon.scanSshKeys && VPSMon.scanSshKeys() },
    { name: 'Add Custom Metric',      icon: '➕', category: 'Actions', action: () => VPSMon.showAddCustomMetric && VPSMon.showAddCustomMetric() },
    { name: 'Open Public Status Page', icon: '🌍', category: 'Actions', action: () => window.open('/status', '_blank'), keys: 'public external' },
    { name: 'Logout',                 icon: '🚪', category: 'Actions', action: () => VPSMon.logout && VPSMon.logout() },

    // Info
    { name: 'Keyboard Shortcuts',     icon: '⌨️', category: 'Help',    action: () => { const m = document.getElementById('shortcuts-modal'); if (m) m.classList.remove('hidden'); }, keys: 'help' },
    { name: 'Refresh page',           icon: '🔄', category: 'Help',    action: () => location.reload() },
  ];

  let root = null;
  let input = null;
  let listEl = null;
  let selected = 0;
  let filtered = COMMANDS;

  function build() {
    root = document.createElement('div');
    root.id = 'cmd-palette';
    root.className = 'hidden';
    root.innerHTML = `
      <div class="cmd-palette-backdrop"></div>
      <div class="cmd-palette-box">
        <div class="cmd-palette-header">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          <input id="cmd-palette-input" type="text" placeholder="Type a command or page name…" autocomplete="off" spellcheck="false">
          <kbd class="cmd-palette-hint">ESC</kbd>
        </div>
        <div id="cmd-palette-list" class="cmd-palette-list"></div>
        <div class="cmd-palette-footer">
          <span><kbd>↑↓</kbd> Navigate</span>
          <span><kbd>↵</kbd> Select</span>
          <span><kbd>⌘K</kbd> Toggle</span>
        </div>
      </div>`;
    document.body.appendChild(root);
    input = root.querySelector('#cmd-palette-input');
    listEl = root.querySelector('#cmd-palette-list');

    root.querySelector('.cmd-palette-backdrop').addEventListener('click', close);
    input.addEventListener('input', () => { selected = 0; render(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowDown') { selected = Math.min(filtered.length - 1, selected + 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { selected = Math.max(0, selected - 1); render(); e.preventDefault(); }
      else if (e.key === 'Enter') { if (filtered[selected]) { run(filtered[selected]); } e.preventDefault(); }
    });
  }

  function filter(q) {
    if (!q) return COMMANDS;
    q = q.toLowerCase();
    return COMMANDS
      .map(c => ({
        cmd: c,
        score: score(c, q),
      }))
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map(x => x.cmd);
  }
  function score(c, q) {
    const hay = (c.name + ' ' + (c.keys || '') + ' ' + (c.category || '')).toLowerCase();
    if (hay.includes(q)) {
      if (c.name.toLowerCase().startsWith(q)) return 100;
      if (c.name.toLowerCase().includes(q))   return 50;
      return 20;
    }
    // fuzzy: all chars in order
    let qi = 0;
    for (let i = 0; i < hay.length && qi < q.length; i++) if (hay[i] === q[qi]) qi++;
    return qi === q.length ? 5 : 0;
  }

  function render() {
    filtered = filter(input.value);
    if (selected >= filtered.length) selected = Math.max(0, filtered.length - 1);
    if (!filtered.length) {
      listEl.innerHTML = `<div class="cmd-palette-empty">No matches</div>`;
      return;
    }
    let lastCat = null;
    const html = filtered.map((c, i) => {
      const catHeader = c.category !== lastCat ? `<div class="cmd-palette-cat">${c.category}</div>` : '';
      lastCat = c.category;
      return `${catHeader}
        <div class="cmd-palette-item ${i === selected ? 'active' : ''}" data-idx="${i}">
          <span class="cmd-palette-icon">${c.icon || '•'}</span>
          <span class="cmd-palette-name">${escapeHtml(c.name)}</span>
        </div>`;
    }).join('');
    listEl.innerHTML = html;
    listEl.querySelectorAll('.cmd-palette-item').forEach(el => {
      el.addEventListener('mouseenter', () => { selected = parseInt(el.dataset.idx); render(); });
      el.addEventListener('click', () => run(filtered[parseInt(el.dataset.idx)]));
    });
    const activeEl = listEl.querySelector('.cmd-palette-item.active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
  }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function run(cmd) {
    close();
    try { cmd.action(); } catch (e) { console.error('palette action:', e); }
  }

  function open() {
    if (!root) build();
    root.classList.remove('hidden');
    input.value = '';
    selected = 0;
    render();
    setTimeout(() => input.focus(), 10);
  }
  function close() { if (root) root.classList.add('hidden'); }
  function toggle() { if (!root || root.classList.contains('hidden')) open(); else close(); }

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      toggle();
    }
  });

  window.VPSMonPalette = { open, close, toggle };
})();
