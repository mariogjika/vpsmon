// VPSMon — Wave 12: Security audit, Reports, Annotations, Prometheus
(function () {
  'use strict';
  function $(s) { return document.querySelector(s); }
  function api(path, opts) {
    return fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {},
      { headers: Object.assign({ 'Content-Type': 'application/json' }, (opts || {}).headers || {}),
        body: (opts && opts.body) ? JSON.stringify(opts.body) : undefined })).then(r => r.json());
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }

  function whenReady(cb) { if (window.VPSMon) cb(); else setTimeout(() => whenReady(cb), 50); }

  whenReady(() => {
    const V = window.VPSMon;

    // ============ Security Audit ============
    async function loadSecurityAudit() {
      try {
        const d = await api('security-audit');
        // Score + Grade
        const gradeEl = $('#security-grade');
        const scoreEl = $('#security-score');
        if (gradeEl) {
          const color = d.score >= 90 ? 'var(--success)' : d.score >= 60 ? 'var(--warning)' : 'var(--danger)';
          gradeEl.textContent = d.grade;
          gradeEl.style.color = color;
          scoreEl.textContent = `Score: ${d.score}/100 — ${d.critical} critical, ${d.warnings} warnings`;
        }
        // Issues list
        const el = $('#security-issues-list');
        if (!el) return;
        if (!d.issues || !d.issues.length) {
          el.innerHTML = '<div class="empty-state" style="color:var(--success)">No issues found. Your setup is secure.</div>';
          return;
        }
        el.innerHTML = d.issues.map(issue => {
          const icon = issue.severity === 'critical' ? '🔴' : issue.severity === 'warning' ? '🟡' : '🔵';
          const badgeClass = issue.severity === 'critical' ? 'badge-critical' : issue.severity === 'warning' ? 'badge-warning' : 'badge-info';
          return `<div class="alert-item" style="border-left:3px solid ${issue.severity === 'critical' ? 'var(--danger)' : issue.severity === 'warning' ? 'var(--warning)' : 'var(--info)'}">
            <div class="alert-item-icon">${icon}</div>
            <div class="alert-item-content">
              <div class="alert-item-msg">${esc(issue.title)}</div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">${esc(issue.description)}</div>
              <div style="font-size:11px;color:var(--accent);margin-top:6px;font-weight:600">${esc(issue.action)}</div>
            </div>
            <div><span class="badge ${badgeClass}">${esc(issue.severity)}</span></div>
          </div>`;
        }).join('');
      } catch (e) {
        const el = $('#security-issues-list');
        if (el) el.innerHTML = '<div class="empty-state">Error loading audit</div>';
      }
    }
    V.refreshSecurityAudit = loadSecurityAudit;

    // ============ Force password change check ============
    async function checkForcePasswordChange() {
      try {
        const d = await api('security-audit');
        const hasCritical = (d.issues || []).some(i =>
          i.severity === 'critical' && i.title.includes('Default password')
        );
        if (hasCritical) {
          // Show a persistent banner
          const banner = document.createElement('div');
          banner.id = 'force-pw-banner';
          banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#dc2626;color:#fff;padding:12px 20px;text-align:center;font-size:13px;font-weight:600;display:flex;align-items:center;justify-content:center;gap:12px';
          banner.innerHTML = `
            ⚠️ You are using the default password. Change it now for security.
            <button onclick="VPSMon.goPage('settings')" style="background:#fff;color:#dc2626;border:none;padding:6px 14px;border-radius:6px;font-weight:700;cursor:pointer;font-size:12px">Change Password</button>
          `;
          if (!document.getElementById('force-pw-banner')) {
            document.body.appendChild(banner);
          }
        }
      } catch {}
    }
    // Check on app load
    setTimeout(checkForcePasswordChange, 2000);

    // ============ Page routing ============
    document.addEventListener('vpsmon:page', (e) => {
      if (e.detail === 'security-audit') loadSecurityAudit();
    });
  });
})();
