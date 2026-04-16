// VPSMon — Wave 9 client: Runbooks, Updates, Backups, SSH keys, Custom metrics
(function () {
  'use strict';
  function $(s, r) { return (r || document).querySelector(s); }
  function api(path, opts) {
    return fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {},
      { headers: Object.assign({ 'Content-Type': 'application/json' }, (opts || {}).headers || {}),
        body: (opts && opts.body) ? JSON.stringify(opts.body) : undefined })).then(r => r.json());
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function fmtTs(t) { return t ? new Date(t * 1000).toLocaleString() : '—'; }
  function timeAgo(t) {
    if (!t) return '—';
    const s = Math.floor(Date.now() / 1000 - t);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  function fmtBytes(n) {
    if (!n) return '—';
    const units = ['B','KB','MB','GB','TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(1) + ' ' + units[i];
  }
  function whenReady(cb) { if (window.VPSMon) cb(); else setTimeout(() => whenReady(cb), 50); }

  whenReady(() => {
    const V = window.VPSMon;
    let _runbookToExecute = null;
    let _editMode = 'new';

    // ============ Runbooks ============
    async function loadRunbooks() {
      const d = await api('runbooks');
      const runs = (await api('runbook-runs?limit=30')).runs || [];
      const byCategory = {};
      (d.runbooks || []).forEach(r => {
        if (!byCategory[r.category]) byCategory[r.category] = [];
        byCategory[r.category].push(r);
      });
      let html = '';
      for (const cat of Object.keys(byCategory).sort()) {
        html += `<div style="margin-top:12px;font-weight:600;color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.5px">${esc(cat)}</div>`;
        html += '<div class="runbook-grid">';
        for (const r of byCategory[cat]) {
          const danger = r.dangerous ? '<span style="color:#ef4444;font-size:10px;margin-left:4px">⚠ DANGER</span>' : '';
          const builtinBadge = r.builtin ? '<span class="badge badge-info" style="font-size:9px">built-in</span>' : '';
          html += `<div class="runbook-card">
            <div class="runbook-card-header">
              <div>
                <div style="font-weight:600;font-size:13px">${esc(r.name)}${danger}</div>
                <div style="font-size:11px;color:#64748b">${esc(r.description || '')}</div>
              </div>
              ${builtinBadge}
            </div>
            <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
              <button class="btn-small btn-primary" onclick="VPSMon.executeRunbook(${r.id})">▶ Execute</button>
              ${!r.builtin ? `<button class="btn-small" onclick="VPSMon.editRunbook(${r.id})">Edit</button>` : ''}
              ${!r.builtin ? `<button class="btn-small btn-danger" onclick="VPSMon.deleteRunbook(${r.id})">Delete</button>` : ''}
            </div>
          </div>`;
        }
        html += '</div>';
      }
      $('#runbooks-list').innerHTML = html || '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No runbooks.</div>';

      // Runs history
      if (!runs.length) {
        $('#runbook-runs').innerHTML = '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No runs yet.</div>';
      } else {
        $('#runbook-runs').innerHTML = `<table class="data-table"><thead><tr><th>Runbook</th><th>Target</th><th>Status</th><th>Duration</th><th>Started</th><th>By</th><th></th></tr></thead><tbody>${
          runs.map(r => {
            const ok = r.exit_code === 0;
            const dur = r.ended_at && r.started_at ? (r.ended_at - r.started_at) + 's' : '—';
            const badge = r.dry_run ? '<span class="badge badge-info">DRY</span>' :
                          ok ? '<span class="badge" style="background:rgba(34,197,94,0.2);color:#22c55e">OK</span>' :
                               `<span class="badge badge-critical">FAIL ${r.exit_code}</span>`;
            return `<tr>
              <td>${esc(r.slug)}</td>
              <td style="font-size:11px">${esc(r.target)}</td>
              <td>${badge}</td>
              <td>${dur}</td>
              <td style="color:#64748b;font-size:11px">${timeAgo(r.started_at)}</td>
              <td style="font-size:11px">${esc(r.triggered_by || '')}</td>
              <td><button class="btn-small" onclick="VPSMon.viewRunbookRun(${r.id})">View</button></td>
            </tr>`;
          }).join('')
        }</tbody></table>`;
      }
    }

    V.showAddRunbook = function () {
      _editMode = 'new';
      $('#rb-edit-title').textContent = 'New Runbook';
      $('#rb-id').value = '';
      $('#rb-name').value = '';
      $('#rb-category').value = 'Custom';
      $('#rb-description').value = '';
      $('#rb-script').value = '#!/bin/bash\necho "Hello from runbook"\n';
      $('#rb-dangerous').checked = false;
      $('#runbook-edit-modal').classList.remove('hidden');
    };
    V.editRunbook = async function (id) {
      const r = await api('runbooks/' + id);
      _editMode = 'edit';
      $('#rb-edit-title').textContent = 'Edit Runbook';
      $('#rb-id').value = r.id;
      $('#rb-name').value = r.name;
      $('#rb-category').value = r.category;
      $('#rb-description').value = r.description;
      $('#rb-script').value = r.script;
      $('#rb-dangerous').checked = !!r.dangerous;
      $('#runbook-edit-modal').classList.remove('hidden');
    };
    V.deleteRunbook = async function (id) {
      if (!confirm('Delete this runbook permanently?')) return;
      await api('runbooks/' + id, { method: 'DELETE' });
      V.toast('Runbook deleted', 'info');
      loadRunbooks();
    };
    V.executeRunbook = async function (id) {
      const r = await api('runbooks/' + id);
      _runbookToExecute = r;
      $('#rb-exec-title').textContent = r.name;
      $('#rb-exec-desc').textContent = r.description;
      $('#rb-exec-danger-warn').classList.toggle('hidden', !r.dangerous);
      $('#rb-exec-output').classList.add('hidden');
      $('#rb-exec-output').innerHTML = '';
      // Populate targets with servers
      const sel = $('#rb-exec-target');
      const serverOpts = (await api('servers')).servers || [];
      sel.innerHTML = `<option value="local">📦 Local (this monitoring server)</option>
        <option value="all">🌐 All enabled remote servers</option>
        ${serverOpts.map(s => `<option value="server:${s.id}">🖥 ${esc(s.name)} (${esc(s.hostname)})</option>`).join('')}`;
      $('#runbook-exec-modal').classList.remove('hidden');
    };
    V.runRunbook = async function (dryRun) {
      if (!_runbookToExecute) return;
      const target = $('#rb-exec-target').value;
      if (_runbookToExecute.dangerous && !dryRun) {
        if (!confirm(`⚠️ "${_runbookToExecute.name}" is marked DANGEROUS.\nProceed executing on: ${target}?`)) return;
      }
      const outEl = $('#rb-exec-output');
      outEl.classList.remove('hidden');
      outEl.innerHTML = '<div class="skeleton-loader" style="height:60px">Executing…</div>';
      const res = await api(`runbooks/${_runbookToExecute.id}/execute`, {
        method: 'POST', body: { target, dry_run: dryRun, timeout: 180 }
      });
      if (!res.ok) {
        outEl.innerHTML = `<div style="color:#ef4444">Error: ${esc(res.error)}</div>`;
        return;
      }
      if (res.dry_run) {
        outEl.innerHTML = `<div style="font-size:12px;color:#94a3b8;margin-bottom:6px">🔍 Dry run — would execute:</div>
          <pre class="run-output">${esc(res.script)}</pre>`;
        return;
      }
      let html = `<div style="font-size:12px;margin-bottom:8px">
        <span class="badge badge-info">${res.total} targets</span>
        ${res.succeeded ? `<span class="badge" style="background:rgba(34,197,94,0.2);color:#22c55e">${res.succeeded} ok</span>` : ''}
        ${res.failed ? `<span class="badge badge-critical">${res.failed} failed</span>` : ''}
      </div>`;
      html += res.results.map(r => `
        <details ${r.exit_code !== 0 ? 'open' : ''} style="margin-bottom:6px">
          <summary style="cursor:pointer;padding:6px;background:#14161d;border-radius:4px">
            <span style="color:${r.exit_code === 0 ? '#22c55e' : '#ef4444'}">${r.exit_code === 0 ? '✓' : '✗ (' + r.exit_code + ')'}</span>
            <b>${esc(r.label)}</b>
            <span style="color:#64748b;font-size:11px">${r.duration_sec}s</span>
          </summary>
          <pre class="run-output">${esc(r.output || '(no output)')}</pre>
        </details>
      `).join('');
      outEl.innerHTML = html;
      loadRunbooks();
    };
    V.viewRunbookRun = async function (id) {
      const r = await api('runbook-runs/' + id);
      _runbookToExecute = { name: r.slug };
      $('#rb-exec-title').textContent = `Run #${r.id} — ${r.slug}`;
      $('#rb-exec-desc').textContent = `Target: ${r.target} · ${timeAgo(r.started_at)}`;
      $('#rb-exec-danger-warn').classList.add('hidden');
      $('#rb-exec-output').classList.remove('hidden');
      $('#rb-exec-output').innerHTML = `<pre class="run-output">${esc(r.output || '')}</pre>`;
      // Hide action buttons since this is historical
      $('#runbook-exec-modal').classList.remove('hidden');
    };

    // ============ Updates ============
    async function loadUpdates() {
      const d = await api('updates');
      const rows = d.updates || [];
      const totalPending = rows.reduce((a, r) => a + (r.total_updates || 0), 0);
      const totalSecurity = rows.reduce((a, r) => a + (r.security_updates || 0), 0);
      const badge = $('#updates-badge');
      if (totalSecurity > 0) { badge.textContent = totalSecurity; badge.classList.remove('hidden'); }
      else badge.classList.add('hidden');

      if (!rows.length) {
        $('#updates-list').innerHTML = '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No scan data yet — click <b>Scan Now</b>.</div>';
        return;
      }
      $('#updates-list').innerHTML = `
        <div class="grid-4col" style="margin-bottom:14px">
          <div class="stat-card"><div class="stat-val">${rows.length}</div><div class="stat-label">Servers scanned</div></div>
          <div class="stat-card"><div class="stat-val" style="color:${totalPending>0?'#f59e0b':'#22c55e'}">${totalPending}</div><div class="stat-label">Pending updates</div></div>
          <div class="stat-card"><div class="stat-val" style="color:${totalSecurity>0?'#ef4444':'#22c55e'}">${totalSecurity}</div><div class="stat-label">Security updates</div></div>
          <div class="stat-card"><div class="stat-val">${Math.floor((Date.now()/1000 - Math.max(...rows.map(r=>r.ts || 0)))/3600)}h</div><div class="stat-label">Last scan</div></div>
        </div>
        <table class="data-table"><thead><tr><th>Server</th><th>Distro</th><th>Total</th><th>Security</th><th>Sample Packages</th><th>Last Scan</th></tr></thead>
        <tbody>${rows.map(r => `<tr>
          <td><b>${esc(r.server_name || '—')}</b></td>
          <td>${esc(r.distro || '')}</td>
          <td><span style="color:${r.total_updates>0?'#f59e0b':'#22c55e'}">${r.total_updates}</span></td>
          <td><span style="color:${r.security_updates>0?'#ef4444':'#22c55e'};font-weight:600">${r.security_updates}</span></td>
          <td style="font-size:11px;color:#94a3b8">${(r.packages_list || []).slice(0, 5).map(esc).join(', ')}${(r.packages_list||[]).length>5?'…':''}</td>
          <td style="color:#64748b">${timeAgo(r.ts)}</td>
        </tr>`).join('')}</tbody></table>`;
    }
    V.scanUpdates = async function () {
      V.toast('Scanning all servers…', 'info', 3000);
      await api('updates/scan', { method: 'POST' });
      V.toast('Update scan complete', 'success');
      loadUpdates();
    };

    // ============ Backups ============
    async function loadBackups() {
      const d = await api('backups');
      const rows = d.backups || [];
      if (!rows.length) {
        $('#backups-list').innerHTML = '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No backups detected. Click <b>Scan Now</b>.</div>';
        return;
      }
      $('#backups-list').innerHTML = `<table class="data-table"><thead><tr><th>Server</th><th>Name</th><th>Tool</th><th>Path</th><th>Last</th><th>Age</th><th>Size</th><th>Health</th></tr></thead>
        <tbody>${rows.map(r => {
          const healthColor = r.health === 'fresh' ? '#22c55e' : r.health === 'warn' ? '#f59e0b' : r.health === 'stale' ? '#ef4444' : '#64748b';
          return `<tr>
            <td><b>${esc(r.server_name || '—')}</b></td>
            <td>${esc(r.name)}</td>
            <td><span class="badge badge-info">${esc(r.tool)}</span></td>
            <td style="font-family:ui-monospace,monospace;font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.path || '')}</td>
            <td style="font-size:11px">${r.last_backup ? fmtTs(r.last_backup) : '—'}</td>
            <td>${r.age_hours != null ? r.age_hours + 'h' : '—'}</td>
            <td>${fmtBytes(r.size_bytes)}</td>
            <td><span style="color:${healthColor};font-weight:600">${esc(r.health)}</span></td>
          </tr>`;
        }).join('')}</tbody></table>`;
    }
    V.scanBackups = async function () {
      V.toast('Scanning backups…', 'info', 3000);
      await api('backups/scan', { method: 'POST' });
      V.toast('Backup scan complete', 'success');
      loadBackups();
    };

    // ============ SSH Keys ============
    async function loadSshKeys() {
      const d = await api('ssh-keys');
      const rows = d.keys || [];
      if (!rows.length) {
        $('#ssh-keys-list').innerHTML = '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No keys scanned yet. Click <b>Scan Now</b>.</div>';
        return;
      }
      const byServer = {};
      rows.forEach(k => {
        const key = k.server_name || 'localhost';
        if (!byServer[key]) byServer[key] = [];
        byServer[key].push(k);
      });
      let html = '';
      for (const srv of Object.keys(byServer).sort()) {
        const keys = byServer[srv];
        html += `<div style="margin-top:14px">
          <h4 style="font-size:13px;color:#cbd5e1;margin-bottom:6px">🖥 ${esc(srv)} <span style="color:#64748b;font-weight:normal">(${keys.length} keys)</span></h4>
          <table class="data-table" style="font-size:12px">
            <thead><tr><th>User</th><th>Type</th><th>Fingerprint</th><th>Comment</th><th>First Seen</th></tr></thead>
            <tbody>${keys.map(k => `<tr>
              <td><b>${esc(k.user)}</b></td>
              <td><span class="badge badge-info">${esc(k.key_type)}</span></td>
              <td style="font-family:ui-monospace,monospace;font-size:11px">${esc(k.fingerprint)}</td>
              <td style="color:#94a3b8">${esc(k.comment || '')}</td>
              <td style="color:#64748b;font-size:11px">${fmtTs(k.first_seen)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
      }
      $('#ssh-keys-list').innerHTML = html;
    }
    V.scanSshKeys = async function () {
      V.toast('Scanning SSH keys…', 'info', 3000);
      await api('ssh-keys/scan', { method: 'POST' });
      V.toast('SSH key scan complete', 'success');
      loadSshKeys();
    };

    // ============ Custom Metrics ============
    async function loadCustomMetrics() {
      const d = await api('custom-metrics');
      const rows = d.metrics || [];
      if (!rows.length) {
        $('#custom-metrics-list').innerHTML = '<div class="empty-state" style="padding:20px;text-align:center;color:#64748b">No custom metrics yet. Click <b>+ Add Metric</b>.</div>';
        return;
      }
      $('#custom-metrics-list').innerHTML = `<table class="data-table"><thead><tr><th>Name</th><th>Label</th><th>Target</th><th>Last Value</th><th>Unit</th><th>Interval</th><th>Last Run</th><th></th></tr></thead>
        <tbody>${rows.map(m => `<tr>
          <td style="font-family:ui-monospace,monospace">${esc(m.name)}</td>
          <td>${esc(m.label)}</td>
          <td><span class="badge badge-info">${esc(m.target)}</span></td>
          <td><b>${m.last_value != null ? m.last_value : '—'}</b></td>
          <td style="color:#64748b">${esc(m.unit || '')}</td>
          <td>${m.interval_sec}s</td>
          <td style="color:${m.last_error?'#ef4444':'#64748b'};font-size:11px">${m.last_error ? '⚠ ' + esc(m.last_error.slice(0,40)) : timeAgo(m.last_ts)}</td>
          <td>
            <button class="btn-small" onclick="VPSMon.runCustomMetric(${m.id})">Run</button>
            <button class="btn-small btn-danger" onclick="VPSMon.deleteCustomMetric(${m.id})">Delete</button>
          </td>
        </tr>`).join('')}</tbody></table>`;
    }
    V.showAddCustomMetric = async function () {
      // Populate target servers
      const servers = (await api('servers')).servers || [];
      $('#cm-target').innerHTML = `<option value="local">Local</option>` +
        servers.map(s => `<option value="server:${s.id}">🖥 ${esc(s.name)}</option>`).join('');
      $('#custom-metric-modal').classList.remove('hidden');
    };
    V.runCustomMetric = async function (id) {
      const r = await api('custom-metrics/' + id + '/run', { method: 'POST' });
      if (r.ok) V.toast(`Value: ${r.value}`, 'success');
      else V.toast('Failed: ' + (r.error || '?'), 'error');
      loadCustomMetrics();
    };
    V.deleteCustomMetric = async function (id) {
      if (!confirm('Delete this custom metric?')) return;
      await api('custom-metrics/' + id, { method: 'DELETE' });
      V.toast('Deleted', 'info');
      loadCustomMetrics();
    };

    // ============ Form handlers ============
    document.addEventListener('submit', (e) => {
      if (e.target.id === 'runbook-edit-form') {
        e.preventDefault();
        const body = {
          name: $('#rb-name').value,
          category: $('#rb-category').value,
          description: $('#rb-description').value,
          script: $('#rb-script').value,
          dangerous: $('#rb-dangerous').checked,
        };
        const id = $('#rb-id').value;
        const req = id ? api('runbooks/' + id, { method: 'PUT', body }) : api('runbooks', { method: 'POST', body });
        req.then(r => {
          if (r.ok) {
            V.toast(id ? 'Runbook updated' : 'Runbook created', 'success');
            $('#runbook-edit-modal').classList.add('hidden');
            loadRunbooks();
          } else V.toast(r.error || 'Error', 'error');
        });
      }
      if (e.target.id === 'custom-metric-form') {
        e.preventDefault();
        const body = {
          name: $('#cm-name').value,
          label: $('#cm-label').value,
          target: $('#cm-target').value,
          script: $('#cm-script').value,
          interval_sec: parseInt($('#cm-interval').value),
          unit: $('#cm-unit').value,
        };
        api('custom-metrics', { method: 'POST', body }).then(r => {
          if (r.ok) {
            V.toast('Custom metric added', 'success');
            $('#custom-metric-modal').classList.add('hidden');
            e.target.reset();
            loadCustomMetrics();
          } else V.toast(r.error || 'Error', 'error');
        });
      }
    });

    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'runbooks') loadRunbooks();
      else if (e.detail === 'updates') loadUpdates();
      else if (e.detail === 'backups') loadBackups();
      // SSH Keys relocated into Servers page
      if (e.detail === 'servers') loadSshKeys();
      // Custom Metrics relocated into System page
      if (e.detail === 'system') loadCustomMetrics();
    });

    // Poll updates badge
    async function pollUpdatesBadge() {
      try {
        const d = await api('updates');
        const total = (d.updates || []).reduce((a, r) => a + (r.security_updates || 0), 0);
        const badge = $('#updates-badge');
        if (badge) {
          if (total) { badge.textContent = total; badge.classList.remove('hidden'); }
          else badge.classList.add('hidden');
        }
      } catch {}
    }
    setInterval(pollUpdatesBadge, 60 * 60 * 1000);  // hourly
    setTimeout(pollUpdatesBadge, 5000);
  });
})();
