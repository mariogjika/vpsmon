// VPSMon — Wave 11: Databases, Web Analytics, SSL Certs, Docker Updates, Dark Mode
(function () {
  'use strict';
  function $(s) { return document.querySelector(s); }
  function api(path, opts) {
    return fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {},
      { headers: Object.assign({ 'Content-Type': 'application/json' }, (opts || {}).headers || {}),
        body: (opts && opts.body) ? JSON.stringify(opts.body) : undefined })).then(r => r.json());
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }
  function fmtBytes(n) { if (!n) return '—'; const u = ['B','KB','MB','GB','TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return n.toFixed(1) + ' ' + u[i]; }
  function fmtNum(n) { if (n == null) return '—'; if (n >= 1e9) return (n/1e9).toFixed(1) + 'B'; if (n >= 1e6) return (n/1e6).toFixed(1) + 'M'; if (n >= 1e3) return (n/1e3).toFixed(1) + 'K'; return String(n); }

  function whenReady(cb) { if (window.VPSMon) cb(); else setTimeout(() => whenReady(cb), 50); }

  whenReady(() => {
    const V = window.VPSMon;

    // ============ Databases ============
    async function loadDatabases() {
      const el = $('#databases-content');
      try {
        const d = await api('databases');
        let html = '';
        for (const [serverName, info] of Object.entries(d)) {
          const snaps = info.snapshots || [];
          if (!snaps.length) continue;
          html += `<div class="card"><h3>${esc(serverName)}</h3>`;
          for (const snap of snaps) {
            const p = snap.parsed || {};
            const type = snap.db_type;
            if (type === 'postgres') {
              html += `<div style="margin-bottom:16px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                  <span class="badge badge-info">PostgreSQL</span>
                  <span style="font-size:11px;color:var(--text-muted)">${esc((p.version||'').split(',')[0])}</span>
                </div>
                <div class="grid-4col">
                  <div class="stat-card"><div class="stat-val">${p.connections || 0}</div><div class="stat-label">Connections</div></div>
                  <div class="stat-card"><div class="stat-val">${p.active_queries || 0}</div><div class="stat-label">Active</div></div>
                  <div class="stat-card"><div class="stat-val">${p.cache_hit_ratio != null ? p.cache_hit_ratio + '%' : '—'}</div><div class="stat-label">Cache Hit</div></div>
                  <div class="stat-card"><div class="stat-val">${fmtBytes(p.db_size_bytes)}</div><div class="stat-label">Size</div></div>
                </div>
                <div class="grid-4col" style="margin-top:6px">
                  <div class="stat-card"><div class="stat-val">${fmtNum(p.transactions_committed)}</div><div class="stat-label">Commits</div></div>
                  <div class="stat-card"><div class="stat-val">${p.deadlocks || 0}</div><div class="stat-label">Deadlocks</div></div>
                  <div class="stat-card"><div class="stat-val">${p.temp_files || 0}</div><div class="stat-label">Temp Files</div></div>
                  <div class="stat-card"><div class="stat-val">${p.replication_lag || 0}s</div><div class="stat-label">Repl Lag</div></div>
                </div>
                ${p.slow_queries && p.slow_queries.length ? `<div style="margin-top:12px"><h4 style="font-size:12px;color:var(--text-secondary);margin-bottom:6px">SLOW QUERIES</h4>
                  ${p.slow_queries.map(q => `<div style="font-size:11px;font-family:var(--mono);padding:6px;background:var(--bg);border-radius:4px;margin-bottom:4px"><span style="color:var(--danger);font-weight:600">${q.duration}s</span> ${esc(q.user)} — ${esc(q.query)}</div>`).join('')}
                </div>` : ''}
              </div>`;
            } else if (type === 'redis') {
              html += `<div style="margin-bottom:16px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                  <span class="badge" style="background:rgba(220,38,38,0.1);color:#dc2626">Redis</span>
                  <span style="font-size:11px;color:var(--text-muted)">${esc(p.version)}</span>
                </div>
                <div class="grid-4col">
                  <div class="stat-card"><div class="stat-val">${p.connected_clients || 0}</div><div class="stat-label">Clients</div></div>
                  <div class="stat-card"><div class="stat-val">${fmtBytes(p.used_memory_bytes)}</div><div class="stat-label">Memory</div></div>
                  <div class="stat-card"><div class="stat-val">${p.hit_ratio || 0}%</div><div class="stat-label">Hit Ratio</div></div>
                  <div class="stat-card"><div class="stat-val">${fmtNum(p.total_keys)}</div><div class="stat-label">Total Keys</div></div>
                </div>
              </div>`;
            } else if (type === 'mysql') {
              html += `<div style="margin-bottom:16px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                  <span class="badge" style="background:rgba(37,99,235,0.1);color:#2563eb">MySQL</span>
                  <span style="font-size:11px;color:var(--text-muted)">${esc(p.version)}</span>
                </div>
                <div class="grid-4col">
                  <div class="stat-card"><div class="stat-val">${p.connections || 0}</div><div class="stat-label">Connections</div></div>
                  <div class="stat-card"><div class="stat-val">${fmtNum(p.questions)}</div><div class="stat-label">Queries</div></div>
                  <div class="stat-card"><div class="stat-val">${p.slow_queries || 0}</div><div class="stat-label">Slow Queries</div></div>
                  <div class="stat-card"><div class="stat-val">${fmtBytes(p.db_size_bytes)}</div><div class="stat-label">Size</div></div>
                </div>
              </div>`;
            }
          }
          html += '</div>';
        }
        el.innerHTML = html || '<div class="empty-state">No databases detected on any server.</div>';
      } catch (e) { el.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`; }
    }
    V.probeAllDbs = async function () {
      V.toast('Probing databases...', 'info', 3000);
      await api('databases/local/probe');
      V.toast('Database probe complete', 'success');
      loadDatabases();
    };

    // ============ Web Analytics ============
    async function loadWebAnalytics() {
      const el = $('#web-analytics-content');
      try {
        const d = await api('web-analytics');
        if (d.error) { el.innerHTML = `<div class="empty-state">${esc(d.error)}</div>`; return; }
        el.innerHTML = `
          <div class="grid-4col">
            <div class="stat-card"><div class="stat-val">${fmtNum(d.requests_24h)}</div><div class="stat-label">Requests 24h</div></div>
            <div class="stat-card"><div class="stat-val">${d.rpm_1h || 0}</div><div class="stat-label">RPM (1h)</div></div>
            <div class="stat-card"><div class="stat-val">${d.error_rate_pct || 0}%</div><div class="stat-label">Error Rate</div></div>
            <div class="stat-card"><div class="stat-val">${d.unique_ips_24h || 0}</div><div class="stat-label">Unique IPs</div></div>
          </div>
          <div class="grid-2col">
            <div class="card">
              <h3>Top Endpoints</h3>
              <table class="data-table"><thead><tr><th>Path</th><th>Hits</th></tr></thead>
              <tbody>${Object.entries(d.top_endpoints || {}).map(([path, count]) =>
                `<tr><td style="font-family:var(--mono);font-size:11px">${esc(path)}</td><td>${fmtNum(count)}</td></tr>`
              ).join('')}</tbody></table>
            </div>
            <div class="card">
              <h3>Status Codes</h3>
              <table class="data-table"><thead><tr><th>Code</th><th>Count</th></tr></thead>
              <tbody>${Object.entries(d.status_codes || {}).map(([code, count]) => {
                const c = parseInt(code);
                const color = c >= 500 ? 'var(--danger)' : c >= 400 ? 'var(--warning)' : c >= 300 ? 'var(--info)' : 'var(--success)';
                return `<tr><td><span style="color:${color};font-weight:700">${code}</span></td><td>${fmtNum(count)}</td></tr>`;
              }).join('')}</tbody></table>
            </div>
          </div>
          <div class="grid-2col">
            <div class="card">
              <h3>Top IPs</h3>
              <table class="data-table"><thead><tr><th>IP</th><th>Requests</th></tr></thead>
              <tbody>${Object.entries(d.top_ips || {}).map(([ip, count]) =>
                `<tr><td style="font-family:var(--mono);font-size:11px">${esc(ip)}</td><td>${fmtNum(count)}</td></tr>`
              ).join('')}</tbody></table>
            </div>
            <div class="card">
              <h3>Recent Errors</h3>
              ${(d.recent_errors || []).length ? `<div style="max-height:300px;overflow:auto">${(d.recent_errors || []).slice(0, 10).map(e =>
                `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
                  <span style="color:var(--danger);font-weight:600">${e.status}</span>
                  <span style="color:var(--text-muted)">${esc(e.method)}</span>
                  <span style="font-family:var(--mono)">${esc(e.path)}</span>
                  <span style="color:var(--text-muted);font-size:10px;float:right">${esc(e.ip)}</span>
                </div>`
              ).join('')}</div>` : '<div class="empty-state">No errors found</div>'}
            </div>
          </div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:8px">Source: ${esc(d.log_file || '')}</div>
        `;
      } catch (e) { el.innerHTML = `<div class="empty-state">Error loading analytics</div>`; }
    }
    V.scanWebAnalytics = async function () {
      V.toast('Scanning access logs...', 'info', 3000);
      await api('web-analytics/scan', { method: 'POST' });
      V.toast('Scan complete', 'success');
      loadWebAnalytics();
    };

    // ============ SSL Certs ============
    async function loadSSLCerts() {
      const el = $('#ssl-certs-list');
      try {
        const d = await api('ssl-certs');
        const certs = d.certs || [];
        if (!certs.length) { el.innerHTML = '<div class="empty-state">No SSL certificates detected. The TLS collector scans configured domains.</div>'; return; }
        el.innerHTML = `<table class="data-table">
          <thead><tr><th>Domain</th><th>Issuer</th><th>Expires</th><th>Days Left</th><th>Status</th></tr></thead>
          <tbody>${certs.map(c => {
            const color = c.status === 'ok' ? 'var(--success)' : c.status === 'warning' ? 'var(--warning)' : 'var(--danger)';
            return `<tr>
              <td style="font-weight:600">${esc(c.domain)}</td>
              <td style="font-size:11px;color:var(--text-muted)">${esc(c.issuer)}</td>
              <td style="font-size:11px">${esc(c.not_after)}</td>
              <td><span style="color:${color};font-weight:700;font-size:16px">${c.days_left != null ? c.days_left : '?'}</span></td>
              <td><span class="badge badge-${c.status === 'ok' ? 'success' : c.status}">${esc(c.status)}</span></td>
            </tr>`;
          }).join('')}</tbody></table>`;
      } catch { el.innerHTML = '<div class="empty-state">Error loading certificates</div>'; }
    }

    // ============ Dark Mode Toggle ============
    V.toggleTheme = function () {
      const isDark = document.body.classList.toggle('dark-mode');
      localStorage.setItem('vpsmon-theme', isDark ? 'dark' : 'light');
      const btn = $('#theme-toggle-btn');
      if (btn) btn.textContent = isDark ? '\u2600' : '\u263E'; // sun : moon
    };
    // Apply saved theme on load
    (function () {
      const saved = localStorage.getItem('vpsmon-theme');
      if (saved === 'dark') {
        document.body.classList.add('dark-mode');
        const btn = document.getElementById('theme-toggle-btn');
        if (btn) btn.textContent = '\u2600';
      }
    })();

    // ============ Page routing ============
    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'databases') loadDatabases();
      else if (e.detail === 'web-analytics') loadWebAnalytics();
      else if (e.detail === 'ssl-certs') loadSSLCerts();
    });
  });
})();
