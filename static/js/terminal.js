// VPSMon — Web Terminal + File Browser client
(function () {
  'use strict';
  function $(s) { return document.querySelector(s); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }
  function fmtBytes(n) { if (!n) return '—'; const u = ['B','KB','MB','GB','TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return n.toFixed(1) + ' ' + u[i]; }
  function fmtTs(t) { return t ? new Date(t * 1000).toLocaleString() : '—'; }

  let term = null;
  let fitAddon = null;
  let ws = null;
  let currentFilePath = '/';

  function whenReady(cb) { if (window.VPSMon) cb(); else setTimeout(() => whenReady(cb), 50); }

  whenReady(() => {
    const V = window.VPSMon;

    // Populate server dropdowns
    async function populateServerSelects() {
      try {
        const d = await fetch('/api/servers', { credentials: 'same-origin' }).then(r => r.json());
        const servers = d.servers || [];
        const opts = `<option value="local">localhost (this server)</option>` +
          servers.filter(s => s.enabled).map(s =>
            `<option value="${s.id}">${esc(s.name)} (${esc(s.hostname)})</option>`
          ).join('');
        const ts = $('#terminal-server-select');
        const fs = $('#files-server-select');
        if (ts) ts.innerHTML = opts;
        if (fs) fs.innerHTML = opts;
      } catch {}
    }

    // ============ Terminal ============
    V.connectTerminal = function () {
      const serverId = $('#terminal-server-select')?.value || 'local';
      if (ws && ws.readyState <= 1) { ws.close(); }

      // Create xterm
      if (term) { term.dispose(); }
      const container = $('#terminal-body');
      container.innerHTML = '';
      term = new window.Terminal({
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'SF Mono', 'Fira Code', monospace",
        theme: { background: '#1a1c24', foreground: '#e2e8f0', cursor: '#6366f1', selectionBackground: 'rgba(99,102,241,0.3)' },
        cursorBlink: true,
        scrollback: 5000,
        allowProposedApi: true,
      });
      fitAddon = new window.FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      term.open(container);
      fitAddon.fit();

      const cols = term.cols;
      const rows = term.rows;

      // WebSocket
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${serverId}?cols=${cols}&rows=${rows}`);
      ws.binaryType = 'arraybuffer';

      $('#terminal-status').textContent = 'Connecting…';
      $('#terminal-status').style.color = '#d97706';

      ws.onopen = () => {
        $('#terminal-status').textContent = 'Connected';
        $('#terminal-status').style.color = '#059669';
        term.focus();
      };

      ws.onmessage = (evt) => {
        if (typeof evt.data === 'string') {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type === 'connected') {
              $('#terminal-server-label').textContent = msg.server + ' (' + msg.host + ')';
            } else if (msg.type === 'error') {
              term.write('\r\n\x1b[31m' + msg.message + '\x1b[0m\r\n');
            }
          } catch {
            term.write(evt.data);
          }
        } else {
          term.write(new Uint8Array(evt.data));
        }
      };

      ws.onclose = () => {
        $('#terminal-status').textContent = 'Disconnected';
        $('#terminal-status').style.color = '#dc2626';
        term.write('\r\n\x1b[90m[connection closed]\x1b[0m\r\n');
      };

      ws.onerror = () => {
        $('#terminal-status').textContent = 'Error';
        $('#terminal-status').style.color = '#dc2626';
      };

      // Terminal input → WebSocket
      term.onData((data) => {
        if (ws && ws.readyState === 1) {
          ws.send(JSON.stringify({ type: 'input', data: data }));
        }
      });

      // Resize
      term.onResize(({ cols, rows }) => {
        if (ws && ws.readyState === 1) {
          ws.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
      });

      // Window resize → refit
      const ro = new ResizeObserver(() => { if (fitAddon) fitAddon.fit(); });
      ro.observe(container);
    };

    V.disconnectTerminal = function () {
      if (ws) { ws.close(); ws = null; }
      $('#terminal-status').textContent = 'Disconnected';
      $('#terminal-status').style.color = '#dc2626';
    };

    // ============ File Browser ============
    V.browsePath = async function (path) {
      const serverId = $('#files-server-select')?.value || 'local';
      currentFilePath = path || '/';
      try {
        const r = await fetch(`/api/files/${serverId}/browse?path=${encodeURIComponent(currentFilePath)}`, { credentials: 'same-origin' }).then(r => r.json());
        if (r.error) { V.toast(r.error, 'error'); return; }
        renderBreadcrumb(r.path);
        renderFileList(r.entries, r.path, serverId);
      } catch (e) {
        V.toast('Browse failed: ' + e.message, 'error');
      }
    };

    function renderBreadcrumb(path) {
      const parts = path.split('/').filter(Boolean);
      let html = `<a href="#" onclick="VPSMon.browsePath('/');return false">/</a>`;
      let current = '';
      for (const p of parts) {
        current += '/' + p;
        const cp = current;
        html += ` / <a href="#" onclick="VPSMon.browsePath('${esc(cp)}');return false">${esc(p)}</a>`;
      }
      $('#file-breadcrumb').innerHTML = html;
    }

    function renderFileList(entries, basePath, serverId) {
      if (!entries.length) {
        $('#file-list').innerHTML = '<div class="empty-state">Empty directory</div>';
        return;
      }
      // Add parent dir entry
      if (basePath !== '/') {
        const parent = basePath.split('/').slice(0, -1).join('/') || '/';
        entries = [{ name: '..', type: 'dir', size: 0, modified: 0, permissions: '', _path: parent }].concat(entries);
      }
      $('#file-list').innerHTML = entries.map(e => {
        const icon = e.type === 'dir' ? '📁' : (e.name.match(/\.(log|txt|md|conf|cfg|yml|yaml|json|xml|env|sh|py|js|html|css)$/i) ? '📄' : '📎');
        const fullPath = e._path || (basePath === '/' ? '/' + e.name : basePath + '/' + e.name);
        const onclick = e.type === 'dir'
          ? `VPSMon.browsePath('${esc(fullPath)}')`
          : `VPSMon.viewFile('${serverId}','${esc(fullPath)}','${esc(e.name)}')`;
        return `<div class="file-item" onclick="${onclick}">
          <span class="file-icon">${icon}</span>
          <span class="file-name">${esc(e.name)}</span>
          <span class="file-size">${e.type === 'dir' ? '—' : fmtBytes(e.size)}</span>
          <span class="file-modified">${e.modified ? fmtTs(e.modified) : ''}</span>
        </div>`;
      }).join('');
    }

    V.viewFile = async function (serverId, path, name) {
      try {
        const r = await fetch(`/api/files/${serverId}/read?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' }).then(r => r.json());
        if (r.error) {
          V.toast(r.error === 'binary file' ? 'Cannot display binary files' : r.error, 'error');
          return;
        }
        const card = $('#file-viewer-card');
        card.classList.remove('hidden');
        $('#file-viewer-name').textContent = name + ' (' + fmtBytes(r.size) + ')';
        $('#file-viewer-content').textContent = r.content;
      } catch (e) {
        V.toast('Read failed: ' + e.message, 'error');
      }
    };

    // Hook into page navigation
    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'terminal') {
        populateServerSelects();
      }
      if (e.detail === 'files') {
        populateServerSelects().then(() => V.browsePath('/'));
      }
    });

    // Init server selects
    setTimeout(populateServerSelects, 1000);
  });
})();
