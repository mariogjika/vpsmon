// VPSMon — Agents + Log Explorer client
(function () {
  'use strict';
  function $(s) { return document.querySelector(s); }
  function api(path, opts) {
    return fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {},
      { headers: Object.assign({ 'Content-Type': 'application/json' }, (opts || {}).headers || {}),
        body: (opts && opts.body) ? JSON.stringify(opts.body) : undefined })).then(r => r.json());
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }
  function fmtTs(t) { return t ? new Date(t * 1000).toLocaleString() : '—'; }
  function timeAgo(t) {
    if (!t) return '—';
    const s = Math.floor(Date.now() / 1000 - t);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function whenReady(cb) { if (window.VPSMon) cb(); else setTimeout(() => whenReady(cb), 50); }

  whenReady(() => {
    const V = window.VPSMon;

    // ============ Agents ============
    async function loadAgents() {
      const [agentsRes, tokensRes] = await Promise.all([
        api('agents'),
        api('enrollment-tokens'),
      ]);
      const agentsList = agentsRes.agents || [];
      const tokensList = tokensRes.tokens || [];

      // Tokens
      const tokensEl = $('#enrollment-tokens-list');
      if (tokensEl) {
        if (!tokensList.length) {
          tokensEl.innerHTML = '<div class="empty-state">No enrollment tokens. Create one to start deploying agents.</div>';
        } else {
          tokensEl.innerHTML = `<table class="data-table"><thead><tr><th>Name</th><th>Token</th><th>Uses</th><th>Expires</th><th>Created</th><th></th></tr></thead>
            <tbody>${tokensList.map(t => `<tr>
              <td style="font-weight:600">${esc(t.name || '(unnamed)')}</td>
              <td style="font-family:var(--mono);font-size:11px">${esc(t.token_preview || '')}</td>
              <td>${t.use_count || 0}${t.max_uses ? ' / ' + t.max_uses : ''}</td>
              <td style="color:${t.expires_at && t.expires_at < Date.now()/1000 ? 'var(--danger)' : 'var(--text-muted)'}">${t.expires_at ? fmtTs(t.expires_at) : 'never'}</td>
              <td style="color:var(--text-muted)">${fmtTs(t.created_at)}</td>
              <td><button class="btn-small btn-danger" onclick="VPSMon.revokeEnrollToken(${t.id})">Revoke</button></td>
            </tr>`).join('')}</tbody></table>`;
        }
      }

      // Agents
      const agentsEl = $('#agents-list');
      if (agentsEl) {
        if (!agentsList.length) {
          agentsEl.innerHTML = '<div class="empty-state">No agents connected yet. Create an enrollment token and install the agent on your servers.</div>';
        } else {
          agentsEl.innerHTML = `<table class="data-table"><thead><tr><th></th><th>Name</th><th>OS</th><th>IP</th><th>Version</th><th>Last Heartbeat</th><th>Tags</th><th></th></tr></thead>
            <tbody>${agentsList.map(a => {
              const online = a.status === 'online';
              const dotClass = online ? 'good' : 'bad';
              return `<tr>
                <td><span class="dot ${dotClass}"></span></td>
                <td style="font-weight:600">${esc(a.display_name || a.hostname)}</td>
                <td><span class="badge badge-info">${esc(a.os_type)}</span> <span style="font-size:11px;color:var(--text-muted)">${esc(a.os_distro || a.os_version || '')}</span></td>
                <td style="font-family:var(--mono);font-size:11px">${esc(a.ip_address || '')}</td>
                <td style="font-size:11px">${esc(a.agent_version || '')}</td>
                <td style="color:${online ? 'var(--success)' : 'var(--danger)'}">${timeAgo(a.last_heartbeat)}</td>
                <td style="font-size:11px;color:var(--text-muted)">${esc(a.tags || '')}</td>
                <td><button class="btn-small btn-danger" onclick="VPSMon.deleteAgent('${esc(a.agent_id)}')">Remove</button></td>
              </tr>`;
            }).join('')}</tbody></table>`;
        }
      }

      // Populate log agent filter
      const logFilter = $('#log-agent-filter');
      if (logFilter) {
        logFilter.innerHTML = '<option value="">All agents</option>' +
          agentsList.map(a => `<option value="${esc(a.agent_id)}">${esc(a.display_name || a.hostname)}</option>`).join('');
      }
    }

    V.createEnrollToken = async function () {
      const name = prompt('Token name (e.g. "production-servers"):');
      if (!name) return;
      const hours = parseInt(prompt('Expires in hours (0 = never):', '24')) || 0;
      const result = await api('enrollment-tokens', { method: 'POST', body: { name, expires_hours: hours } });
      if (result.token) {
        const token = result.token;
        // Show the token
        const msg = `Enrollment token created!\n\nToken: ${token}\n\nUse this in the install command:\n--token ${token}`;
        alert(msg);
        navigator.clipboard.writeText(token).then(() => V.toast('Token copied to clipboard', 'success'));
        loadAgents();
      } else {
        V.toast('Failed to create token', 'error');
      }
    };

    V.revokeEnrollToken = async function (id) {
      if (!confirm('Revoke this enrollment token?')) return;
      await api('enrollment-tokens/' + id, { method: 'DELETE' });
      V.toast('Token revoked', 'info');
      loadAgents();
    };

    V.deleteAgent = async function (agentId) {
      if (!confirm('Remove this agent? Historical data will be deleted.')) return;
      await api('agents/' + agentId, { method: 'DELETE' });
      V.toast('Agent removed', 'info');
      loadAgents();
    };

    // ============ Log Explorer ============
    V.searchLogs = async function () {
      const agentId = $('#log-agent-filter')?.value || '';
      const severity = $('#log-severity-filter')?.value || '';
      const query = $('#log-search-input')?.value || '';

      const params = new URLSearchParams();
      if (agentId) params.set('agent_id', agentId);
      if (severity) params.set('severity', severity);
      if (query) params.set('q', query);
      params.set('hours', '24');
      params.set('limit', '200');

      const [logsRes, statsRes] = await Promise.all([
        api('agent-logs?' + params.toString()),
        api('agent-logs/stats?hours=24'),
      ]);

      // Stats
      const statsEl = $('#log-stats');
      if (statsEl) {
        const s = statsRes || {};
        const bySev = s.by_severity || {};
        statsEl.innerHTML = `
          <div class="stat-card"><div class="stat-val">${s.total || 0}</div><div class="stat-label">Total (24h)</div></div>
          <div class="stat-card"><div class="stat-val" style="color:var(--danger)">${(bySev.error || 0) + (bySev.critical || 0) + (bySev.emergency || 0)}</div><div class="stat-label">Errors</div></div>
          <div class="stat-card"><div class="stat-val" style="color:var(--warning)">${bySev.warning || 0}</div><div class="stat-label">Warnings</div></div>
          <div class="stat-card"><div class="stat-val">${bySev.info || 0}</div><div class="stat-label">Info</div></div>
        `;
      }

      // Results
      const logs = logsRes.logs || [];
      const el = $('#log-explorer-results');
      if (!logs.length) {
        el.innerHTML = '<div class="empty-state">No logs found for this search.</div>';
        return;
      }
      el.innerHTML = `<div class="log-output" style="max-height:600px">${logs.map(l => {
        const sevColor = {emergency:'#dc2626',critical:'#dc2626',error:'#ef4444',warning:'#fbbf24',notice:'#60a5fa',info:'#e2e8f0',debug:'#64748b'}[l.severity] || '#e2e8f0';
        return `<div class="log-line"><span class="log-ts">${fmtTs(l.ts)}</span><span style="color:${sevColor};font-weight:600;min-width:60px;display:inline-block">${esc(l.severity || '').toUpperCase()}</span><span class="log-unit">${esc(l.service || '')}</span><span class="log-msg">${esc(l.message || '')}</span></div>`;
      }).join('')}</div>`;
    };

    // ============ Routing ============
    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'agents') loadAgents();
      if (e.detail === 'log-explorer') V.searchLogs();
    });

    // Auto-refresh agents every 15s
    setInterval(() => {
      if (location.hash === '#agents') loadAgents();
    }, 15000);
  });
})();
