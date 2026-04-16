// VPSMon — Wave 8 client: Uptime, Incidents, Tokens, Forecast
(function () {
  'use strict';

  function $(s, r) { return (r || document).querySelector(s); }
  function api(path, opts) { return fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {}, { headers: Object.assign({ 'Content-Type': 'application/json' }, (opts || {}).headers || {}), body: (opts && opts.body) ? JSON.stringify(opts.body) : undefined })).then(r => r.json()); }
  function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function fmtTs(t) { return t ? new Date(t * 1000).toLocaleString() : '—'; }
  function timeAgo(t) {
    if (!t) return '—';
    const s = Math.floor(Date.now() / 1000 - t);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  // Waiting for VPSMon global from app.js
  function whenReady(cb) {
    if (window.VPSMon) cb();
    else setTimeout(() => whenReady(cb), 50);
  }

  whenReady(() => {

    // --- Extend VPSMon global ---
    const V = window.VPSMon;

    V.showAddUptime = function () {
      $('#add-uptime-modal').classList.remove('hidden');
      $('#up-name').focus();
    };

    V.deleteUptime = async function (id) {
      if (!confirm('Delete this uptime check?')) return;
      await api('uptime/checks/' + id, { method: 'DELETE' });
      V.toast('Check deleted', 'info');
      loadUptime();
    };

    V.toggleUptime = async function (id, enabled) {
      await api('uptime/checks/' + id, { method: 'PUT', body: { enabled: enabled ? 1 : 0 } });
      loadUptime();
    };

    V.ackIncident = async function (id) {
      await api('incidents/' + id + '/ack', { method: 'POST' });
      V.toast('Incident acknowledged', 'success');
      loadIncidents();
    };

    V.savePostmortem = async function (id) {
      const text = prompt('Postmortem / notes:');
      if (!text) return;
      await api('incidents/' + id + '/postmortem', { method: 'POST', body: { text } });
      V.toast('Postmortem saved', 'success');
      loadIncidents();
    };

    let _lastTokenRaw = '';
    V.copyToken = function () {
      navigator.clipboard.writeText(_lastTokenRaw).then(() => V.toast('Token copied', 'success'));
    };
    V.createToken = async function () {
      V.goPage('settings');
      setTimeout(() => $('#token-name')?.focus(), 100);
    };
    V.revokeToken = async function (id) {
      if (!confirm('Revoke this token? Scripts using it will stop working.')) return;
      await api('tokens/' + id, { method: 'DELETE' });
      V.toast('Token revoked', 'info');
      loadTokens();
    };

    V.logout = async function () {
      await api('auth/logout', { method: 'POST' });
      location.reload();
    };

    // --- Uptime page ---
    async function loadUptime() {
      const d = await api('uptime/checks');
      const checks = d.checks || [];
      const sum = d.summary || {};
      $('#uptime-summary').innerHTML = `
        <div class="stat-card"><div class="stat-val">${sum.total || 0}</div><div class="stat-label">Total</div></div>
        <div class="stat-card"><div class="stat-val" style="color:#22c55e">${sum.up || 0}</div><div class="stat-label">Up</div></div>
        <div class="stat-card"><div class="stat-val" style="color:${(sum.down || 0) > 0 ? '#ef4444' : '#64748b'}">${sum.down || 0}</div><div class="stat-label">Down</div></div>
        <div class="stat-card"><div class="stat-val">${sum.sla_24h != null ? sum.sla_24h + '%' : '—'}</div><div class="stat-label">24h SLA</div></div>
      `;
      if (!checks.length) {
        $('#uptime-list').innerHTML = '<div class="empty-state" style="padding:30px;text-align:center;color:#64748b">No checks yet. Click <b>+ Add Check</b> to monitor an HTTP endpoint.</div>';
        return;
      }
      $('#uptime-list').innerHTML = `<table class="data-table">
        <thead><tr><th></th><th>Name</th><th>URL</th><th>Status</th><th>Latency</th><th>Last Check</th><th></th></tr></thead>
        <tbody>${checks.map(c => {
          const up = !!c.last_up;
          const dot = `<span class="dot ${up?'good':'bad'}"></span>`;
          const latency = c.last_latency_ms != null ? c.last_latency_ms + 'ms' : '';
          const statusText = up ? 'UP' : 'DOWN';
          const err = c.last_error ? `<div style="font-size:11px;color:#ef4444">${escapeHtml(c.last_error)}</div>` : '';
          return `<tr>
            <td>${dot}</td>
            <td><div style="font-weight:500">${escapeHtml(c.name)}</div>${c.tags ? `<div style="font-size:10px;color:#64748b">${escapeHtml(c.tags)}</div>` : ''}</td>
            <td style="font-family:ui-monospace,monospace;font-size:11px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.url)}</td>
            <td style="color:${up?'#22c55e':'#ef4444'};font-weight:600">${statusText}${err}</td>
            <td>${latency}</td>
            <td style="color:#64748b">${timeAgo(c.last_checked)}</td>
            <td style="text-align:right">
              <button class="btn-small" onclick="VPSMon.toggleUptime(${c.id}, ${c.enabled ? 0 : 1})">${c.enabled ? 'Pause' : 'Resume'}</button>
              <button class="btn-small btn-danger" onclick="VPSMon.deleteUptime(${c.id})">Delete</button>
            </td>
          </tr>`;
        }).join('')}</tbody></table>`;
    }

    // --- Incidents page ---
    async function loadIncidents() {
      const [openRes, listRes, sumRes] = await Promise.all([
        api('incidents/open'),
        api('incidents?days=30'),
        api('incidents/summary?days=7'),
      ]);
      const openList = openRes.incidents || [];
      const recent = (listRes.incidents || []).filter(i => i.resolved);
      const s = sumRes || {};
      $('#incidents-summary').innerHTML = `
        <div class="stat-card"><div class="stat-val" style="color:${openList.length>0?'#ef4444':'#22c55e'}">${openList.length}</div><div class="stat-label">Open Now</div></div>
        <div class="stat-card"><div class="stat-val">${s.total || 0}</div><div class="stat-label">7d Total</div></div>
        <div class="stat-card"><div class="stat-val" style="color:#ef4444">${s.critical || 0}</div><div class="stat-label">7d Critical</div></div>
        <div class="stat-card"><div class="stat-val">${s.mttr_minutes != null ? s.mttr_minutes + 'm' : '—'}</div><div class="stat-label">MTTR</div></div>
      `;
      // Badge in sidebar
      const badge = $('#incidents-badge');
      if (badge) {
        if (openList.length) { badge.textContent = openList.length; badge.classList.remove('hidden'); }
        else badge.classList.add('hidden');
      }
      $('#incidents-open-list').innerHTML = openList.length ? openList.map(renderIncident).join('') : '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No open incidents. ✓</div>';
      $('#incidents-recent-list').innerHTML = recent.length ? recent.slice(0, 20).map(renderIncident).join('') : '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No incidents in last 30 days.</div>';
    }
    function renderIncident(i) {
      const durSec = i.duration || (i.resolved ? 0 : Math.floor(Date.now() / 1000 - i.started_at));
      const dur = durSec > 3600 ? (durSec / 3600).toFixed(1) + 'h' : Math.floor(durSec / 60) + 'm';
      return `<div class="incident-item">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <span class="badge badge-${i.severity}">${i.severity}</span>
            <span style="font-weight:500">${escapeHtml(i.title)}</span>
            ${i.acked_by ? `<span style="color:#64748b;font-size:11px"> (ack ${escapeHtml(i.acked_by)})</span>` : ''}
          </div>
          <div style="font-size:11px;color:#64748b;white-space:nowrap">${timeAgo(i.started_at)} · ${dur}</div>
        </div>
        ${i.message ? `<div style="font-size:12px;color:#94a3b8;margin-top:4px">${escapeHtml(i.message)}</div>` : ''}
        ${i.postmortem ? `<div style="font-size:11px;color:#64748b;margin-top:4px;padding:6px;background:#14161d;border-radius:4px"><b>Postmortem:</b> ${escapeHtml(i.postmortem)}</div>` : ''}
        <div style="margin-top:6px;display:flex;gap:6px">
          ${!i.resolved && !i.acked_by ? `<button class="btn-small" onclick="VPSMon.ackIncident(${i.id})">Acknowledge</button>` : ''}
          <button class="btn-small" onclick="VPSMon.savePostmortem(${i.id})">${i.postmortem ? 'Edit' : 'Add'} Notes</button>
        </div>
      </div>`;
    }

    // --- Tokens (settings tab) ---
    async function loadTokens() {
      const d = await api('tokens');
      const list = d.tokens || [];
      const el = $('#tokens-list'); if (!el) return;
      if (!list.length) { el.innerHTML = '<div class="empty-state" style="padding:16px;text-align:center;color:#64748b">No tokens yet.</div>'; return; }
      el.innerHTML = `<table class="data-table" style="font-size:12px">
        <thead><tr><th>Name</th><th>Prefix</th><th>Scope</th><th>Created</th><th>Last used</th><th>Expires</th><th></th></tr></thead>
        <tbody>${list.map(t => `<tr>
          <td>${escapeHtml(t.name)}</td>
          <td style="font-family:ui-monospace,monospace">${escapeHtml(t.prefix || '—')}</td>
          <td><span class="badge badge-${t.scopes === 'full' ? 'warning' : 'info'}">${escapeHtml(t.scopes)}</span></td>
          <td>${fmtTs(t.created_at)}</td>
          <td>${t.last_used ? timeAgo(t.last_used) : 'never'}</td>
          <td>${t.expires_at ? fmtTs(t.expires_at) : 'never'}</td>
          <td><button class="btn-small btn-danger" onclick="VPSMon.revokeToken(${t.id})">Revoke</button></td>
        </tr>`).join('')}</tbody></table>`;
    }

    // --- Forecast (overlay in Overview page) ---
    async function loadForecast() {
      try {
        const d = await api('forecast');
        const widget = $('#forecast-widget'); if (!widget) return;
        const items = [];
        const describe = (key, label) => {
          const f = d[key];
          if (!f || f.error || f.hits_target_at == null) {
            items.push(`<div class="forecast-item"><div class="forecast-label">${label}</div><div class="forecast-val" style="color:#64748b">No trend</div></div>`);
            return;
          }
          const days = f.days_until;
          const color = days < 7 ? '#ef4444' : days < 30 ? '#f59e0b' : '#22c55e';
          items.push(`<div class="forecast-item"><div class="forecast-label">${label}</div><div class="forecast-val" style="color:${color}">${days < 1 ? '< 1 day' : days.toFixed(1) + ' days'}</div><div class="forecast-conf">${f.confidence} confidence</div></div>`);
        };
        describe('disk_full', '💾 Disk fills');
        describe('memory_saturation', '🧠 Memory saturates');
        describe('swap_saturation', '🔁 Swap saturates');
        widget.innerHTML = items.join('');
      } catch {}
    }

    // --- Hook into page navigation ---
    // Audit log loader (now in Settings page)
    async function loadAuditInSettings() {
      try {
        const d = await api('audit?hours=168');
        const events = d.events || d || [];
        const el = document.querySelector('#audit-list');
        if (!el) return;
        if (!events.length) { el.innerHTML = '<div class="empty-state">No audit events in the last 7 days.</div>'; return; }
        el.innerHTML = `<table class="data-table" style="font-size:12px">
          <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Resource</th><th>IP</th></tr></thead>
          <tbody>${events.slice(0, 50).map(e => `<tr>
            <td style="color:#8f95a3;font-size:11px">${fmtTs(e.ts)}</td>
            <td><b>${escapeHtml(e.username || '')}</b></td>
            <td><span class="badge badge-info">${escapeHtml(e.action || '')}</span></td>
            <td style="font-size:11px">${escapeHtml(e.resource || '')}</td>
            <td style="font-size:11px;color:#8f95a3">${escapeHtml(e.ip || '')}</td>
          </tr>`).join('')}</tbody></table>`;
      } catch {}
    }

    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'uptime') loadUptime();
      else if (e.detail === 'incidents') loadIncidents();
      if (e.detail === 'settings') { loadTokens(); loadAuditInSettings(); }
      if (e.detail === 'overview') loadForecast();
    });

    // Auto-refresh open uptime/incidents every 15s
    setInterval(() => {
      if (location.hash === '#uptime') loadUptime();
      if (location.hash === '#incidents') loadIncidents();
    }, 15000);

    // Poll open incidents for badge every 30s
    async function pollIncidentBadge() {
      try {
        const d = await api('incidents/open');
        const n = (d.incidents || []).length;
        const badge = $('#incidents-badge');
        if (badge) {
          if (n) { badge.textContent = n; badge.classList.remove('hidden'); }
          else badge.classList.add('hidden');
        }
      } catch {}
    }
    setInterval(pollIncidentBadge, 30000);
    setTimeout(pollIncidentBadge, 2000);

    // Add Uptime form submit
    document.addEventListener('submit', (e) => {
      if (e.target.id === 'add-uptime-form') {
        e.preventDefault();
        const body = {
          name: $('#up-name').value,
          url: $('#up-url').value,
          method: $('#up-method').value,
          expect_status: parseInt($('#up-status').value),
          expect_body: $('#up-body').value,
          interval_sec: parseInt($('#up-interval').value),
          timeout_sec: parseInt($('#up-timeout').value),
          tags: $('#up-tags').value,
        };
        api('uptime/checks', { method: 'POST', body }).then(r => {
          if (r.ok) {
            V.toast('Uptime check added', 'success');
            $('#add-uptime-modal').classList.add('hidden');
            e.target.reset();
            loadUptime();
          } else V.toast(r.error || 'Error', 'error');
        });
      }
      if (e.target.id === 'token-create-form') {
        e.preventDefault();
        const body = {
          name: $('#token-name').value,
          scopes: $('#token-scope').value,
          days: parseInt($('#token-days').value),
        };
        api('tokens', { method: 'POST', body }).then(r => {
          if (r.token) {
            _lastTokenRaw = r.token;
            $('#token-shown-value').textContent = r.token;
            $('#token-shown-modal').classList.remove('hidden');
            e.target.reset();
            $('#token-days').value = 365;
            loadTokens();
          } else V.toast('Failed to create token', 'error');
        });
      }
    });

    // Initial forecast on load (overview is default page)
    setTimeout(loadForecast, 1500);
  });
})();
