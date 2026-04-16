/* VPSMon Frontend v2 — 50+ improvements */
(function() {
    'use strict';

    // --- State ---
    let ws = null, wsTimer = null, currentPage = 'overview', systemRange = 1800, securityRange = 3600;
    let processTab = 'by_cpu', logAutoScroll = true, charts = {}, sparkData = { cpu: [], mem: [] };
    let dockerView = 'list', lastSystemData = null, logLineCount = 0;

    // --- DOM Helpers ---
    const $ = s => document.querySelector(s);
    const $$ = s => document.querySelectorAll(s);
    const h = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };

    // --- Formatters ---
    function fmtBytes(b) {
        if (!b) return '0 B';
        const s = ['B','KB','MB','GB','TB'];
        const i = Math.floor(Math.log(Math.abs(b)) / Math.log(1024));
        return (b / Math.pow(1024, i)).toFixed(1) + ' ' + s[i];
    }
    function fmtRate(b) { return b ? fmtBytes(b) + '/s' : '0 B/s'; }
    function fmtUptime(s) {
        if (!s) return '-';
        const d = Math.floor(s/86400), hr = Math.floor((s%86400)/3600), m = Math.floor((s%3600)/60);
        return d > 0 ? `${d}d ${hr}h` : hr > 0 ? `${hr}h ${m}m` : `${m}m`;
    }
    function fmtTime(ts) { return ts ? new Date(ts*1000).toLocaleTimeString() : '-'; }
    function fmtDateTime(ts) { return ts ? new Date(ts*1000).toLocaleString() : '-'; }
    function fmtAgo(ts) {
        const diff = Math.floor(Date.now()/1000 - ts);
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff/60) + 'm ago';
        if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
        return Math.floor(diff/86400) + 'd ago';
    }

    // --- Toast Notifications ---
    function toast(msg, type = 'info', duration = 4000) {
        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.textContent = msg;
        $('#toast-container').appendChild(el);
        setTimeout(() => { el.classList.add('removing'); setTimeout(() => el.remove(), 300); }, duration);
    }

    // --- Simple Canvas Charts ---
    class MiniChart {
        constructor(container, opts = {}) {
            this.container = typeof container === 'string' ? $(container) : container;
            if (!this.container) return;
            this.canvas = document.createElement('canvas');
            this.container.innerHTML = '';
            this.container.appendChild(this.canvas);
            this.ctx = this.canvas.getContext('2d');
            this.color = opts.color || '#6366f1';
            this.fillColor = opts.fillColor || 'rgba(99,102,241,0.1)';
            this.suffix = opts.suffix || '';
            this.formatFn = opts.formatFn || null;
            this.data = [];
            this._resize();
            this._ro = new ResizeObserver(() => this._resize());
            this._ro.observe(this.container);
        }
        _resize() {
            const r = this.container.getBoundingClientRect(), dpr = window.devicePixelRatio||1;
            this.canvas.width = r.width*dpr; this.canvas.height = r.height*dpr;
            this.canvas.style.width = r.width+'px'; this.canvas.style.height = r.height+'px';
            this.ctx.scale(dpr,dpr); this.w = r.width; this.h = r.height; this.draw();
        }
        update(data) { this.data = data; this.draw(); }
        draw() {
            const ctx=this.ctx, w=this.w, h=this.h;
            if (!w||!h||!this.data.length) return;
            ctx.clearRect(0,0,w,h);
            const p={top:20,right:10,bottom:25,left:50}, cw=w-p.left-p.right, ch=h-p.top-p.bottom;
            const vals=this.data.map(d=>d.value);
            let min=Math.min(...vals), max=Math.max(...vals);
            if(min===max){min=0;max=max||1;}
            const range=max-min;
            ctx.strokeStyle='rgba(42,45,58,0.8)'; ctx.lineWidth=0.5;
            for(let i=0;i<=4;i++){
                const y=p.top+(ch/4)*i;
                ctx.beginPath();ctx.moveTo(p.left,y);ctx.lineTo(w-p.right,y);ctx.stroke();
                const v=max-(range/4)*i;
                ctx.fillStyle='#5c6078';ctx.font='10px -apple-system,sans-serif';ctx.textAlign='right';
                ctx.fillText(this.formatFn?this.formatFn(v):v.toFixed(1)+this.suffix,p.left-6,y+3);
            }
            const ts=this.data.map(d=>d.ts), tr=ts[ts.length-1]-ts[0];
            ctx.fillStyle='#5c6078';ctx.textAlign='center';
            for(let i=0;i<5;i++){
                const idx=Math.floor((this.data.length-1)*(i/4)), x=p.left+(cw*(i/4)), t=ts[idx];
                if(t){const d=new Date(t*1000);ctx.fillText(tr>86400*3?(d.getMonth()+1)+'/'+d.getDate():d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0'),x,h-5);}
            }
            ctx.beginPath();ctx.strokeStyle=this.color;ctx.lineWidth=1.5;ctx.lineJoin='round';
            for(let i=0;i<this.data.length;i++){
                const x=p.left+(i/(this.data.length-1))*cw, y=p.top+ch-((this.data[i].value-min)/range)*ch;
                i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
            }
            ctx.stroke();
            ctx.lineTo(p.left+cw,p.top+ch);ctx.lineTo(p.left,p.top+ch);ctx.closePath();
            ctx.fillStyle=this.fillColor;ctx.fill();
            if(this.data.length>0){
                const lv=vals[vals.length-1],lb=this.formatFn?this.formatFn(lv):lv.toFixed(1)+this.suffix;
                ctx.fillStyle=this.color;ctx.font='bold 12px -apple-system,sans-serif';ctx.textAlign='right';
                ctx.fillText(lb,w-p.right,14);
            }
        }
        destroy(){if(this._ro)this._ro.disconnect();}
    }

    // --- Sparkline ---
    function drawSparkline(el, data, color = '#6366f1') {
        if (!el || !data.length) return;
        let canvas = el.querySelector('canvas');
        if (!canvas) { canvas = document.createElement('canvas'); canvas.className = 'sparkline-canvas'; el.innerHTML = ''; el.appendChild(canvas); }
        const r = el.getBoundingClientRect(), dpr = window.devicePixelRatio||1;
        canvas.width = r.width*dpr; canvas.height = r.height*dpr;
        canvas.style.width = r.width+'px'; canvas.style.height = r.height+'px';
        const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
        const w=r.width, ht=r.height;
        let min=Math.min(...data), max=Math.max(...data);
        if(min===max){min=0;max=max||1;}
        ctx.clearRect(0,0,w,ht);
        ctx.beginPath(); ctx.strokeStyle=color; ctx.lineWidth=1.5; ctx.lineJoin='round';
        for(let i=0;i<data.length;i++){
            const x=(i/(data.length-1))*w, y=ht-((data[i]-min)/(max-min))*ht*0.8-ht*0.1;
            i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        }
        ctx.stroke();
    }

    // --- API ---
    async function api(path, opts = {}) {
        const resp = await fetch('/api/'+path, {
            ...opts, headers: {'Content-Type':'application/json',...opts.headers},
            body: opts.body ? JSON.stringify(opts.body) : undefined,
        });
        if (resp.status === 401 && !path.startsWith('auth/')) { showLogin(); throw new Error('Unauthorized'); }
        return resp.json();
    }

    // --- Auth ---
    async function checkAuth() {
        try {
            const d = await api('auth/check');
            d.authenticated ? showApp(d.username) : showLogin();
        } catch { showLogin(); }
    }
    function showLogin() {
        $('#login-screen').classList.remove('hidden');
        $('#app').classList.add('hidden');
        disconnectWs();
    }
    function showApp(username) {
        $('#login-screen').classList.add('hidden');
        $('#app').classList.remove('hidden');
        $('#sidebar-user').textContent = username;
        $('#user-avatar').textContent = username.charAt(0).toUpperCase();
        connectWs();
        loadSystemInfo();
        navigateTo(window.location.hash.slice(1) || 'overview');
    }

    // --- System Info Bar ---
    async function loadSystemInfo() {
        try {
            const info = await api('system/info');
            $('#sysinfo-hostname').textContent = info.hostname || '';
            $('#sysinfo-os').textContent = info.os_info || '';
            $('#sysinfo-kernel').textContent = 'Kernel: ' + (info.kernel || '');
            $('#sysinfo-cpu').textContent = (info.cpu_count || '?') + ' cores';
            // Settings page detail
            const el = $('#system-info-detail');
            if (el) el.innerHTML = Object.entries(info).map(([k,v]) =>
                `<div class="info-row"><span class="info-key">${h(k)}</span><span class="info-val">${h(String(v))}</span></div>`
            ).join('');
        } catch {}
    }
    function updateClock() {
        const el = $('#sysinfo-time');
        if (el) el.textContent = new Date().toLocaleTimeString();
    }
    setInterval(updateClock, 1000);

    // --- WebSocket ---
    function connectWs() {
        if (ws && ws.readyState <= 1) return;
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/ws`);
        ws.onopen = () => {
            $('#ws-status').classList.replace('disconnected','connected');
            $('#ws-status').title = 'Connected';
            if (currentPage === 'logs') ws.send(JSON.stringify({cmd:'subscribe',channels:['logs']}));
            if (currentPage === 'security') ws.send(JSON.stringify({cmd:'subscribe',channels:['security']}));
        };
        ws.onmessage = e => { try { handleWsMsg(JSON.parse(e.data)); } catch {} };
        ws.onclose = () => {
            $('#ws-status').classList.replace('connected','disconnected');
            $('#ws-status').title = 'Disconnected';
            clearTimeout(wsTimer);
            wsTimer = setTimeout(connectWs, 3000);
        };
        ws.onerror = () => ws.close();
    }
    function disconnectWs() { clearTimeout(wsTimer); if(ws)ws.close(); ws=null; }

    function handleWsMsg(msg) {
        const {channel, data} = msg;
        // Flash refresh indicator
        const dot = $('#refresh-indicator');
        if(dot){dot.classList.add('active');setTimeout(()=>dot.classList.remove('active'),500);}
        switch(channel) {
            case 'system': updateSystem(data); break;
            case 'docker': updateDocker(data); break;
            case 'processes': updateProcesses(data); break;
            case 'network': updateNetwork(data); break;
            case 'log': appendLog(data); break;
            case 'logs_history': loadLogHistory(data); break;
            case 'security_event': appendSecurityEvent(data); break;
            case 'security_history': loadSecurityHistory(data); break;
            case 'alert': case 'alerts': handleAlert(data); break;
        }
    }

    // --- Gauge ---
    function updateGauge(id, pct) {
        const el = $(id); if(!el) return;
        pct = Math.min(100, Math.max(0, pct||0));
        let color = pct > 80 ? 'var(--danger)' : pct > 60 ? 'var(--warning)' : 'var(--accent)';
        el.style.background = `conic-gradient(${color} ${pct*3.6}deg, var(--bg-input) ${pct*3.6}deg)`;
        el.querySelector('span').textContent = Math.round(pct)+'%';
    }

    // --- System Updates ---
    function updateSystem(data) {
        if (!data || !data._ts) return;
        lastSystemData = data;
        $('#last-update').textContent = 'Updated ' + fmtAgo(data._ts);

        updateGauge('#gauge-cpu', data.cpu_percent);
        updateGauge('#gauge-mem', data.mem_percent);
        updateGauge('#gauge-disk', data.disk_percent);
        updateGauge('#gauge-swap', data.swap_percent);

        // Disk/swap detail
        const dd = $('#disk-detail');
        if(dd) dd.textContent = fmtBytes(data.disk_used)+' / '+fmtBytes(data.disk_total);
        const sd = $('#swap-detail');
        if(sd && data.swap_total > 0) sd.textContent = fmtBytes(data.swap_used)+' / '+fmtBytes(data.swap_total);
        else if(sd) sd.textContent = 'No swap';

        // Sparklines
        sparkData.cpu.push(data.cpu_percent||0);
        sparkData.mem.push(data.mem_percent||0);
        if(sparkData.cpu.length > 30) sparkData.cpu.shift();
        if(sparkData.mem.length > 30) sparkData.mem.shift();
        drawSparkline($('#spark-cpu'), sparkData.cpu, '#6366f1');
        drawSparkline($('#spark-mem'), sparkData.mem, '#06b6d4');

        // Stats
        $('#stat-load').textContent = `${(data.load_1||0).toFixed(2)} / ${(data.load_5||0).toFixed(2)} / ${(data.load_15||0).toFixed(2)}`;
        $('#stat-uptime').textContent = fmtUptime(data.uptime);
        $('#stat-net-rx').textContent = fmtRate(data.net_rx_rate);
        $('#stat-net-tx').textContent = fmtRate(data.net_tx_rate);
        $('#stat-connections').textContent = data.tcp_connections||0;

        // Update favicon badge with CPU
        updateFaviconBadge(Math.round(data.cpu_percent||0));
    }

    // --- Docker ---
    function updateDocker(data) {
        if (currentPage === 'overview') renderOverviewDocker(data);
        if (currentPage === 'docker') renderDockerPage(data);
    }

    function renderOverviewDocker(data) {
        const el = $('#overview-docker'); if(!el) return;
        if (!data.available) { el.innerHTML = '<p class="text-muted pad-16">Docker not available</p>'; return; }
        const cs = data.containers||[];
        if (!cs.length) { el.innerHTML = '<p class="text-muted pad-16">No containers</p>'; return; }
        el.innerHTML = cs.slice(0,8).map(c => {
            const sc = 'status-'+(c.status||'unknown');
            return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:12px;border-bottom:1px solid var(--border)">
                <span class="status-dot ${sc}"></span>
                <span style="font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h(c.name)}</span>
                <span class="text-muted">${c.status}</span>
                ${c.cpu_percent!=null?`<span style="color:var(--cyan);font-variant-numeric:tabular-nums">${c.cpu_percent.toFixed(1)}%</span>`:''}
            </div>`;
        }).join('') + (cs.length > 8 ? `<div class="text-muted" style="padding:4px 0">+${cs.length-8} more</div>` : '');
    }

    function renderDockerPage(data) {
        if (!data.available) { $('#docker-unavailable').classList.remove('hidden'); $('#docker-container-list').innerHTML=''; return; }
        $('#docker-unavailable').classList.add('hidden');
        const cs = data.containers||[];
        if (dockerView === 'compose') {
            renderDockerCompose(cs);
        } else {
            $('#docker-container-list').innerHTML = cs.map(renderContainerCard).join('');
        }
    }

    function renderDockerCompose(containers) {
        const groups = {};
        containers.forEach(c => {
            const proj = c.compose_project || '_standalone';
            if (!groups[proj]) groups[proj] = [];
            groups[proj].push(c);
        });
        let html = '';
        for (const [name, cs] of Object.entries(groups).sort()) {
            const label = name === '_standalone' ? 'Standalone Containers' : name;
            html += `<div class="compose-group">
                <div class="compose-group-header"><span class="compose-icon">&#9654;</span> ${h(label)} <span class="text-muted">(${cs.length})</span></div>
                ${cs.map(renderContainerCard).join('')}
            </div>`;
        }
        $('#docker-container-list').innerHTML = html;
    }

    function renderContainerCard(c) {
        const sc = 'status-'+(c.status||'unknown'), run = c.status === 'running';
        return `<div class="container-card">
            <div class="container-info">
                <div class="container-name"><span class="status-dot ${sc}"></span>${h(c.name)}</div>
                <div class="container-image">${h(c.image)} | ${c.id}${c.compose_service?' | service: '+h(c.compose_service):''}</div>
            </div>
            ${run?`<div class="container-stats">
                <div class="container-stat"><div class="container-stat-val">${(c.cpu_percent||0).toFixed(1)}%</div><div class="container-stat-label">CPU</div></div>
                <div class="container-stat"><div class="container-stat-val">${fmtBytes(c.mem_usage)}</div><div class="container-stat-label">Memory</div></div>
                <div class="container-stat"><div class="container-stat-val">${c.pids||0}</div><div class="container-stat-label">PIDs</div></div>
            </div>`:`<div class="container-stats"><span class="text-muted">${c.status}</span></div>`}
            <div class="container-actions">
                <button class="btn-small" onclick="VPSMon.dockerLogs('${c.id}','${h(c.name)}')">Logs</button>
                ${run?`<button class="btn-small btn-warning" onclick="VPSMon.dockerAction('${c.id}','restart')">Restart</button>
                       <button class="btn-small btn-danger" onclick="VPSMon.dockerAction('${c.id}','stop')">Stop</button>`
                    :`<button class="btn-small btn-success" onclick="VPSMon.dockerAction('${c.id}','start')">Start</button>`}
            </div>
        </div>`;
    }

    // --- Processes ---
    function updateProcesses(data) {
        if (!data) return;
        const c = data.total||0;
        const el = $('#process-count'); if(el) el.textContent = c;
        if (currentPage === 'overview') { $('#stat-processes').textContent = c; return; }
        if (currentPage !== 'processes') return;
        let procs = processTab === 'by_cpu' ? (data.by_cpu||[]) : (data.by_mem||[]);
        const search = ($('#process-search')||{}).value?.toLowerCase();
        if (search) procs = procs.filter(p => p.name.toLowerCase().includes(search) || p.cmd.toLowerCase().includes(search) || String(p.pid).includes(search));
        const tb = $('#process-tbody'); if(!tb) return;
        tb.innerHTML = procs.map(p => `<tr>
            <td>${p.pid}</td><td title="${h(p.cmd)}">${h(p.name)}</td><td>${h(p.user)}</td>
            <td style="color:${p.cpu>50?'var(--danger)':p.cpu>20?'var(--warning)':'inherit'}">${(p.cpu||0).toFixed(1)}%</td>
            <td>${(p.mem_pct||0).toFixed(1)}%</td><td>${fmtBytes(p.mem_rss)}</td><td>${p.threads}</td><td>${p.status}</td>
        </tr>`).join('');
    }

    // --- Logs ---
    function appendLog(entry) {
        if (currentPage !== 'logs') return;
        const el = $('#log-output'); if(!el) return;
        const filter = ($('#log-filter')||{}).value?.toLowerCase()||'';
        const pf = ($('#log-priority')||{}).value;
        if (filter && !entry.message.toLowerCase().includes(filter) && !(entry.unit||'').toLowerCase().includes(filter)) return;
        if (pf && entry.priority > parseInt(pf)) return;
        const div = document.createElement('div');
        div.className = 'log-line priority-'+entry.priority;
        div.innerHTML = `<span class="log-ts">${fmtTime(entry.ts)}</span> <span class="log-unit">[${h(entry.unit||'?')}]</span> <span class="log-msg">${h(entry.message||'')}</span>`;
        el.appendChild(div);
        logLineCount++;
        while(el.children.length > 1000) { el.removeChild(el.firstChild); logLineCount--; }
        const lc = $('#log-count'); if(lc) lc.textContent = logLineCount + ' lines';
        if(logAutoScroll) el.scrollTop = el.scrollHeight;
    }
    function loadLogHistory(entries) {
        const el = $('#log-output'); if(!el) return;
        el.innerHTML = ''; logLineCount = 0;
        (entries||[]).forEach(e => {
            const div = document.createElement('div');
            div.className = 'log-line priority-'+e.priority;
            div.innerHTML = `<span class="log-ts">${fmtTime(e.ts)}</span> <span class="log-unit">[${h(e.unit||'?')}]</span> <span class="log-msg">${h(e.message||'')}</span>`;
            el.appendChild(div); logLineCount++;
        });
        el.scrollTop = el.scrollHeight;
        const lc = $('#log-count'); if(lc) lc.textContent = logLineCount + ' lines';
    }

    // --- Security ---
    function appendSecurityEvent(event) {
        if (currentPage !== 'security') return;
        const tb = $('#security-tbody'); if(!tb) return;
        const tr = document.createElement('tr');
        const sc = event.severity==='warning'?'badge-warning':event.severity==='danger'?'badge-danger':'badge-info';
        tr.innerHTML = `<td>${fmtDateTime(event.ts)}</td><td>${h(event.type||'')}</td>
            <td><span class="badge ${sc}">${event.severity||'info'}</span></td>
            <td>${h(event.username||'')}</td><td>${h(event.source_ip||'')}</td><td>${h(event.message||'')}</td>`;
        tb.insertBefore(tr, tb.firstChild);
    }
    function loadSecurityHistory(events) {
        const tb = $('#security-tbody'); if(!tb) return;
        tb.innerHTML = (events||[]).map(e => {
            const sc = e.severity==='warning'?'badge-warning':e.severity==='danger'||e.severity==='critical'?'badge-danger':'badge-info';
            return `<tr><td>${fmtDateTime(e.ts)}</td><td>${h(e.event_type||e.type||'')}</td>
                <td><span class="badge ${sc}">${e.severity||'info'}</span></td>
                <td>${h(e.username||'')}</td><td>${h(e.source_ip||'')}</td><td>${h(e.message||'')}</td></tr>`;
        }).join('');
    }
    async function loadSecurityPage() {
        const hrs = Math.ceil(securityRange / 3600);
        const d = await api(`security/history?hours=${hrs}`);
        loadSecurityHistory(d.events||[]);
        const ev = d.events||[];
        const logins = ev.filter(e=>e.event_type==='ssh_login').length;
        const fails = ev.filter(e=>e.event_type==='ssh_fail'||e.event_type==='ssh_invalid_user').length;
        const sudos = ev.filter(e=>e.event_type==='sudo').length;
        const ips = new Set(ev.filter(e=>e.source_ip).map(e=>e.source_ip)).size;
        $('#security-stats').innerHTML = `
            <div class="stat-card"><div class="stat-value" style="color:var(--success)">${logins}</div><div class="stat-label">SSH Logins</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--danger)">${fails}</div><div class="stat-label">Failed Attempts</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--warning)">${sudos}</div><div class="stat-label">Sudo Commands</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--info)">${ips}</div><div class="stat-label">Unique IPs</div></div>`;
    }

    // --- Network ---
    function updateNetwork(data) {
        if (currentPage !== 'network') return;
        const est = $('#net-established'); if(est) est.textContent = data.total_established||0;
        const cc = $('#conn-count'); if(cc) cc.textContent = (data.total_established||0) + ' connections';
        const search = ($('#network-search')||{}).value?.toLowerCase()||'';
        const lt = $('#listening-tbody');
        if(lt && data.listening) {
            lt.innerHTML = data.listening.map(c=>`<tr><td>${h(c.laddr)}</td><td>${h(c.process)}</td><td>${c.pid||''}</td><td>${c.type}</td></tr>`).join('');
        }
        const ct = $('#connections-tbody');
        if(ct && data.connections) {
            let conns = data.connections;
            if(search) conns = conns.filter(c=>(c.laddr+c.raddr+c.process).toLowerCase().includes(search));
            ct.innerHTML = conns.slice(0,150).map(c=>`<tr><td>${h(c.laddr)}</td><td>${h(c.raddr)}</td><td>${c.status}</td><td>${h(c.process)}</td><td>${c.pid||''}</td></tr>`).join('');
        }
    }

    // --- Alerts ---
    function handleAlert(data) {
        if (data && data.message) {
            toast(data.message, data.severity === 'critical' ? 'error' : 'warning', 6000);
        }
        loadAlertBadge();
        if (currentPage === 'alerts') loadAlertsPage();
        if (currentPage === 'overview') loadOverviewAlerts();
    }
    async function loadAlertBadge() {
        try {
            const d = await api('alerts');
            const count = (d.alerts||[]).length;
            const badge = $('#alert-badge');
            if(badge) { badge.textContent = count; badge.classList.toggle('hidden', count === 0); }
            const banner = $('#alert-banner');
            if(banner && count > 0) {
                const latest = d.alerts[0];
                $('#alert-banner-text').textContent = `${count} active alert${count>1?'s':''}: ${latest.message}`;
                banner.classList.remove('hidden');
            } else if(banner) banner.classList.add('hidden');
        } catch {}
    }
    async function loadOverviewAlerts() {
        try {
            const d = await api('alerts');
            const el = $('#overview-alerts'); if(!el) return;
            const alerts = d.alerts||[];
            if (!alerts.length) { el.innerHTML = '<p class="text-muted pad-16">No active alerts</p>'; return; }
            el.innerHTML = alerts.slice(0,5).map(a => {
                const icon = a.severity==='critical'?'&#9888;':'&#9679;';
                const color = a.severity==='critical'?'var(--danger)':'var(--warning)';
                return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:12px;border-bottom:1px solid var(--border)">
                    <span style="color:${color}">${icon}</span>
                    <span style="flex:1">${h(a.message)}</span>
                    <span class="text-muted">${fmtAgo(a.ts)}</span>
                </div>`;
            }).join('');
        } catch {}
    }
    async function loadAlertsPage() {
        try {
            const [active, history] = await Promise.all([api('alerts'), api('alerts/history?hours=24')]);
            const al = active.alerts||[];
            const ha = history.alerts||[];
            // Stats
            const critical = al.filter(a=>a.severity==='critical').length;
            const warning = al.filter(a=>a.severity==='warning').length;
            const total24h = ha.length;
            const acked = ha.filter(a=>a.acknowledged).length;
            $('#alert-stats').innerHTML = `
                <div class="stat-card"><div class="stat-value" style="color:var(--danger)">${critical}</div><div class="stat-label">Critical</div></div>
                <div class="stat-card"><div class="stat-value" style="color:var(--warning)">${warning}</div><div class="stat-label">Warning</div></div>
                <div class="stat-card"><div class="stat-value">${total24h}</div><div class="stat-label">Total (24h)</div></div>
                <div class="stat-card"><div class="stat-value" style="color:var(--success)">${acked}</div><div class="stat-label">Acknowledged</div></div>`;
            // Active alerts
            const ael = $('#active-alerts-list');
            if(ael) {
                if(!al.length) { ael.innerHTML = '<p class="text-muted pad-16">No active alerts</p>'; }
                else { ael.innerHTML = al.map(a => `<div class="alert-item">
                    <span class="alert-item-icon" style="color:${a.severity==='critical'?'var(--danger)':'var(--warning)'}">&#9888;</span>
                    <div class="alert-item-content"><div class="alert-item-msg">${h(a.message)}</div><div class="alert-item-time">${fmtDateTime(a.ts)} (${fmtAgo(a.ts)})</div></div>
                    <div class="alert-item-actions"><button class="btn-small btn-success" onclick="VPSMon.ackAlert(${a.id})">Acknowledge</button></div>
                </div>`).join(''); }
            }
            // History
            const ht = $('#alert-history-tbody');
            if(ht) {
                ht.innerHTML = ha.map(a => {
                    const sc = a.severity==='critical'?'badge-critical':a.severity==='warning'?'badge-warning':'badge-info';
                    const status = a.acknowledged ? `<span class="badge badge-success">Acked by ${h(a.ack_by||'?')}</span>` : '<span class="badge badge-warning">Active</span>';
                    return `<tr><td>${fmtDateTime(a.ts)}</td><td>${h(a.alert_type)}</td><td><span class="badge ${sc}">${a.severity}</span></td>
                        <td>${h(a.metric_name)}</td><td>${a.current_value!=null?a.current_value.toFixed(1):'-'}</td>
                        <td>${a.threshold!=null?a.threshold.toFixed(1):'-'}</td><td>${h(a.message)}</td><td>${status}</td></tr>`;
                }).join('');
            }
        } catch(e) { console.error('loadAlertsPage', e); }
    }

    // --- Charts ---
    async function loadSystemCharts() {
        const ms = [
            {m:'cpu_percent',el:'#sys-cpu-chart',color:'#6366f1',suffix:'%'},
            {m:'mem_percent',el:'#sys-mem-chart',color:'#06b6d4',suffix:'%'},
            {m:'swap_percent',el:'#sys-swap-chart',color:'#f59e0b',suffix:'%'},
            {m:'disk_percent',el:'#sys-disk-chart',color:'#22c55e',suffix:'%'},
            {m:'disk_read_rate',el:'#sys-diskio-chart',color:'#3b82f6',formatFn:fmtRate},
            {m:'net_rx_rate',el:'#sys-netrx-chart',color:'#06b6d4',formatFn:fmtRate},
            {m:'net_tx_rate',el:'#sys-nettx-chart',color:'#f59e0b',formatFn:fmtRate},
            {m:'load_1',el:'#sys-load-chart',color:'#ef4444',suffix:''},
        ];
        for (const m of ms) {
            try {
                const d = await api(`system/history?metric=${m.m}&duration=${systemRange}`);
                if(d.data&&d.data.length>0) {
                    if(!charts[m.el]) charts[m.el]=new MiniChart(m.el,{color:m.color,fillColor:m.color+'1a',suffix:m.suffix||'',formatFn:m.formatFn});
                    charts[m.el].update(d.data);
                }
            } catch {}
        }
    }
    async function loadOverviewCharts() {
        for (const m of [
            {m:'cpu_percent',el:'#overview-cpu-chart',color:'#6366f1',suffix:'%'},
            {m:'mem_percent',el:'#overview-mem-chart',color:'#06b6d4',suffix:'%'},
            {m:'net_rx_rate',el:'#overview-net-chart',color:'#06b6d4',formatFn:fmtRate},
        ]) {
            try {
                const d = await api(`system/history?metric=${m.m}&duration=3600`);
                if(d.data&&d.data.length>0) {
                    if(!charts[m.el]) charts[m.el]=new MiniChart(m.el,{color:m.color,fillColor:m.color+'1a',suffix:m.suffix||'',formatFn:m.formatFn});
                    charts[m.el].update(d.data);
                }
            } catch {}
        }
    }

    // --- Favicon Badge ---
    function updateFaviconBadge(value) {
        const canvas = document.createElement('canvas'); canvas.width=32; canvas.height=32;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = value > 80 ? '#ef4444' : value > 60 ? '#f59e0b' : '#6366f1';
        ctx.beginPath(); ctx.arc(16,16,14,0,Math.PI*2); ctx.fill();
        ctx.fillStyle='white'; ctx.font='bold 14px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(value>99?'!':String(value),16,17);
        let link = document.querySelector("link[rel*='icon']");
        if(!link){link=document.createElement('link');link.rel='icon';document.head.appendChild(link);}
        link.href = canvas.toDataURL();
    }

    // --- Navigation ---
    function navigateTo(page) {
        if (!page) page = 'overview';
        currentPage = page;
        $$('.page').forEach(p=>p.classList.remove('active'));
        const t = $(`#page-${page}`); if(t) t.classList.add('active');
        $$('.nav-link').forEach(l=>l.classList.remove('active'));
        const lk = $(`.nav-link[data-page="${page}"]`); if(lk) lk.classList.add('active');
        window.location.hash = page;
        // Close mobile sidebar
        $('#sidebar').classList.remove('open');
        // WS subscriptions
        if(ws&&ws.readyState===1) {
            ws.send(JSON.stringify({cmd:page==='logs'?'subscribe':'unsubscribe',channels:['logs']}));
            ws.send(JSON.stringify({cmd:page==='security'?'subscribe':'unsubscribe',channels:['security']}));
        }
        // Page-specific loads
        if(page==='overview'){loadOverviewCharts();loadOverviewAlerts();loadAlertBadge();}
        if(page==='system') loadSystemCharts();
        if(page==='security') loadSecurityPage();
        if(page==='docker') api('docker').then(d=>renderDockerPage(d));
        if(page==='network') api('network').then(d=>updateNetwork(d));
        if(page==='alerts') loadAlertsPage();
        document.dispatchEvent(new CustomEvent('vpsmon:page', {detail: page}));
    }

    // --- Keyboard Shortcuts ---
    function handleKeyboard(e) {
        if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT') return;
        const pages=['overview','system','docker','processes','logs','security','network','alerts','settings'];
        const n = parseInt(e.key);
        if(n>=1&&n<=9&&pages[n-1]) { navigateTo(pages[n-1]); e.preventDefault(); return; }
        if(e.key==='?') { $('#shortcuts-modal').classList.toggle('hidden'); e.preventDefault(); }
        if(e.key==='Escape') { $('#shortcuts-modal').classList.add('hidden'); $('#docker-logs-panel').classList.add('hidden'); }
        if(e.key==='r'&&!e.ctrlKey&&!e.metaKey) { navigateTo(currentPage); toast('Refreshed','info',1500); }
        if(e.key==='a') navigateTo('alerts');
        if(e.key==='/') {
            e.preventDefault();
            const s = $('#process-search')||$('#log-filter')||$('#network-search');
            if(s) s.focus();
        }
    }

    // --- Global API ---
    window.VPSMon = {
        goPage: navigateTo,
        closeModal() { $('#shortcuts-modal').classList.add('hidden'); },
        async dockerLogs(id, name) {
            const d = await api(`docker/${id}/logs?tail=200`);
            $('#docker-logs-name').textContent = name;
            $('#docker-logs-content').textContent = d.logs||'No logs';
            $('#docker-logs-panel').classList.remove('hidden');
        },
        async dockerAction(id, action) {
            toast(`${action}ing container...`, 'info', 2000);
            await api(`docker/${id}/${action}`, {method:'POST'});
            setTimeout(()=>api('docker').then(d=>renderDockerPage(d)), 1500);
        },
        async ackAlert(id) {
            await api(`alerts/${id}/ack`, {method:'POST'});
            toast('Alert acknowledged','success');
            loadAlertsPage(); loadAlertBadge(); loadOverviewAlerts();
        },
        async ackAllAlerts() {
            const d = await api('alerts');
            for(const a of d.alerts||[]) await api(`alerts/${a.id}/ack`, {method:'POST'});
            toast('All alerts acknowledged','success');
            loadAlertsPage(); loadAlertBadge(); loadOverviewAlerts();
        },
        exportChart(metric, duration) {
            window.open(`/api/metrics/export?metric=${metric}&duration=${duration}&format=csv`, '_blank');
        },
        exportCurrentMetric(metric) {
            window.open(`/api/metrics/export?metric=${metric}&duration=${systemRange}&format=csv`, '_blank');
        },
        toast,

        // --- PWA / Push ---
        async refreshPushStatus() {
            const status = document.querySelector('#push-status');
            const subBtn = document.querySelector('#push-subscribe-btn');
            const unsubBtn = document.querySelector('#push-unsubscribe-btn');
            const testBtn = document.querySelector('#push-test-btn');
            if (!status) return;
            if (!window.VPSMonPush || !VPSMonPush.supported) {
                status.innerHTML = '<span style="color:#f59e0b">Push notifications are not supported in this browser.</span>';
                return;
            }
            const isSub = await VPSMonPush.isSubscribed();
            if (isSub) {
                status.innerHTML = `<span style="color:#22c55e">✓ Push enabled on this device</span>`;
                subBtn?.classList.add('hidden');
                unsubBtn?.classList.remove('hidden');
                testBtn?.classList.remove('hidden');
            } else {
                const perm = Notification.permission;
                status.innerHTML = perm === 'denied'
                    ? '<span style="color:#ef4444">Notifications blocked — change browser settings to enable</span>'
                    : 'Push is available but not enabled on this device.';
                subBtn?.classList.remove('hidden');
                unsubBtn?.classList.add('hidden');
                testBtn?.classList.add('hidden');
            }
        },
        async pushSubscribe() {
            try {
                await VPSMonPush.subscribe();
                toast('Push notifications enabled', 'success');
                this.refreshPushStatus();
            } catch (e) {
                toast(e.message || 'Failed to enable push', 'error');
            }
        },
        async pushUnsubscribe() {
            try {
                await VPSMonPush.unsubscribe();
                toast('Push notifications disabled', 'info');
                this.refreshPushStatus();
            } catch (e) {
                toast(e.message || 'Failed to disable push', 'error');
            }
        },
        async pushTest() {
            try {
                const r = await fetch('/api/push/test', { method:'POST' });
                const d = await r.json();
                toast(`Test sent to ${d.delivered || 0} device(s)`, 'success');
            } catch (e) {
                toast('Test push failed', 'error');
            }
        },
        async installApp() {
            if (!window.VPSMonPush) return;
            const res = await VPSMonPush.showInstall();
            if (res === 'accepted') toast('App installed!', 'success');
        },
    };

    // --- Keyboard shortcuts (global) ---
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const map = {
            '1':'dashboard','2':'system','3':'docker','4':'processes',
            '5':'logs','6':'security','7':'network','8':'alerts',
            's':'servers','n':'notifications','a':'audit','g':'settings'
        };
        if (map[e.key]) { navigateTo(map[e.key]); }
        else if (e.key === '?') { const m = document.getElementById('shortcuts-modal'); if (m) m.classList.remove('hidden'); }
        else if (e.key === 'Escape') { document.querySelectorAll('.modal:not(.hidden)').forEach(m => m.classList.add('hidden')); }
    });

    // Refresh push status when settings page opens
    document.addEventListener('vpsmon:page', (e) => {
        if (e.detail === 'settings' && window.VPSMon && VPSMon.refreshPushStatus) {
            setTimeout(() => VPSMon.refreshPushStatus(), 100);
        }
    });

    // --- Init ---
    function init() {
        // Login
        $('#login-form').addEventListener('submit', async e => {
            e.preventDefault();
            const btn = $('#login-btn');
            btn.querySelector('.btn-text').textContent = 'Signing in...';
            btn.querySelector('.btn-spinner').classList.remove('hidden');
            btn.disabled = true;
            try {
                const d = await api('auth/login',{method:'POST',body:{username:$('#login-user').value,password:$('#login-pass').value}});
                if(d.ok) { showApp(d.username); toast('Welcome back!','success'); }
                else { $('#login-error').textContent=d.error||'Login failed'; $('#login-error').classList.remove('hidden'); }
            } catch { $('#login-error').textContent='Connection error'; $('#login-error').classList.remove('hidden'); }
            finally { btn.querySelector('.btn-text').textContent='Sign In'; btn.querySelector('.btn-spinner').classList.add('hidden'); btn.disabled=false; }
        });

        // Logout
        $('#logout-btn').addEventListener('click', async()=>{ await api('auth/logout',{method:'POST'}); showLogin(); toast('Logged out','info'); });

        // Nav
        $$('.nav-link').forEach(l=>l.addEventListener('click',e=>{e.preventDefault();navigateTo(l.dataset.page);}));

        // System time range
        $$('#page-system .time-btn').forEach(b=>b.addEventListener('click',()=>{
            $$('#page-system .time-btn').forEach(x=>x.classList.remove('active'));
            b.classList.add('active'); systemRange=parseInt(b.dataset.range); loadSystemCharts();
        }));

        // Security time range
        $$('#security-time-range .time-btn').forEach(b=>b.addEventListener('click',()=>{
            $$('#security-time-range .time-btn').forEach(x=>x.classList.remove('active'));
            b.classList.add('active'); securityRange=parseInt(b.dataset.range); loadSecurityPage();
        }));

        // Process tabs
        $$('.tab-btn').forEach(b=>b.addEventListener('click',()=>{
            $$('.tab-btn').forEach(x=>x.classList.remove('active'));
            b.classList.add('active'); processTab=b.dataset.tab;
        }));

        // Log controls
        $('#log-autoscroll').addEventListener('change',e=>{logAutoScroll=e.target.checked;});
        $('#log-clear-btn').addEventListener('click',()=>{const el=$('#log-output');if(el){el.innerHTML='';logLineCount=0;}});

        // Docker view toggle
        $('#docker-view-list')?.addEventListener('click',()=>{dockerView='list';$('#docker-view-list').classList.add('active');$('#docker-view-compose').classList.remove('active');api('docker').then(d=>renderDockerPage(d));});
        $('#docker-view-compose')?.addEventListener('click',()=>{dockerView='compose';$('#docker-view-compose').classList.add('active');$('#docker-view-list').classList.remove('active');api('docker').then(d=>renderDockerPage(d));});

        // Docker logs close
        $('#docker-logs-close').addEventListener('click',()=>$('#docker-logs-panel').classList.add('hidden'));

        // Alert banner close
        $('#alert-banner-close')?.addEventListener('click',()=>$('#alert-banner').classList.add('hidden'));

        // Ack all alerts
        $('#ack-all-btn')?.addEventListener('click',()=>VPSMon.ackAllAlerts());

        // Mobile menu
        $('#mobile-menu-btn')?.addEventListener('click',()=>$('#sidebar').classList.toggle('open'));

        // Settings - password change
        $('#change-password-form')?.addEventListener('submit',async e=>{
            e.preventDefault();
            const np=$('#new-password').value, cp=$('#confirm-password').value;
            if(np!==cp){toast('Passwords do not match','error');return;}
            try{await api('auth/change-password',{method:'POST',body:{password:np}});toast('Password updated','success');$('#new-password').value='';$('#confirm-password').value='';}
            catch{toast('Failed to update password','error');}
        });

        // Theme toggle
        $('#theme-dark')?.addEventListener('click',()=>{document.documentElement.removeAttribute('data-theme');$('#theme-dark').classList.add('active');$('#theme-light').classList.remove('active');localStorage.setItem('theme','dark');});
        $('#theme-light')?.addEventListener('click',()=>{document.documentElement.setAttribute('data-theme','light');$('#theme-light').classList.add('active');$('#theme-dark').classList.remove('active');localStorage.setItem('theme','light');});

        // Load saved theme
        if(localStorage.getItem('theme')==='light'){document.documentElement.setAttribute('data-theme','light');$('#theme-light')?.classList.add('active');$('#theme-dark')?.classList.remove('active');}

        // Keyboard shortcuts
        document.addEventListener('keydown', handleKeyboard);

        // Hash nav
        window.addEventListener('hashchange',()=>navigateTo(window.location.hash.slice(1)));

        // Periodic alert badge refresh
        setInterval(loadAlertBadge, 30000);

        checkAuth();
    }

    document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();

/* ============================================================
   EXTENSION: Multi-server, Notifications, Audit, 2FA, Users
   Hooks into the IIFE via window.VPSMon
   ============================================================ */
(function() {
    'use strict';
    const $ = s => document.querySelector(s);
    const $$ = s => document.querySelectorAll(s);
    const h = s => { const d=document.createElement('div'); d.textContent=s==null?'':String(s); return d.innerHTML; };
    const fmtBytes = b => { if(!b)return '0 B'; const u=['B','KB','MB','GB','TB']; const i=Math.floor(Math.log(Math.abs(b))/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(1)+' '+u[i]; };
    const fmtAgo = ts => { const d=Math.floor(Date.now()/1000-ts); if(d<60)return 'just now'; if(d<3600)return Math.floor(d/60)+'m ago'; if(d<86400)return Math.floor(d/3600)+'h ago'; return Math.floor(d/86400)+'d ago'; };
    const fmtDT = ts => ts ? new Date(ts*1000).toLocaleString() : '-';
    const api = async (path, opts={}) => {
        const r = await fetch('/api/'+path, {...opts, headers:{'Content-Type':'application/json',...opts.headers}, body: opts.body?JSON.stringify(opts.body):undefined});
        return r.json();
    };

    // --- SERVERS ---
    async function loadServers() {
        try {
            const [d, ov] = await Promise.all([api('servers'), api('servers/overview')]);
            const servers = d.servers || [];
            $('#servers-overview-stats').innerHTML = `
                <div class="stat-card"><div class="stat-value">${ov.total||0}</div><div class="stat-label">Total Servers</div></div>
                <div class="stat-card"><div class="stat-value" style="color:var(--success)">${ov.online||0}</div><div class="stat-label">Online</div></div>
                <div class="stat-card"><div class="stat-value" style="color:var(--danger)">${ov.offline||0}</div><div class="stat-label">Offline</div></div>
                <div class="stat-card"><div class="stat-value">${(ov.avg_cpu_percent||0).toFixed(1)}%</div><div class="stat-label">Avg CPU</div></div>
                <div class="stat-card"><div class="stat-value">${(ov.avg_mem_percent||0).toFixed(1)}%</div><div class="stat-label">Avg Memory</div></div>
                <div class="stat-card"><div class="stat-value">${(ov.avg_disk_percent||0).toFixed(1)}%</div><div class="stat-label">Avg Disk</div></div>`;
            const el = $('#servers-list');
            if (!servers.length) {
                el.innerHTML = '<p class="text-muted pad-16">No servers configured. Click "Add Server" to start monitoring your fleet.</p>';
                return;
            }
            el.innerHTML = servers.map(s => {
                const m = s.metrics || {};
                const st = s.status || {};
                const dot = st.online ? 'status-running' : 'status-exited';
                const cpu = m.cpu_percent != null ? m.cpu_percent.toFixed(1)+'%' : '-';
                const mem = m.mem_percent != null ? m.mem_percent.toFixed(1)+'%' : '-';
                const disk = m.disk_percent != null ? m.disk_percent.toFixed(1)+'%' : '-';
                const load = m.load_1 != null ? m.load_1.toFixed(2) : '-';
                return `<div class="container-card">
                    <div class="container-info">
                        <div class="container-name"><span class="status-dot ${dot}"></span>${h(s.name)}</div>
                        <div class="container-image">${h(s.hostname)}:${s.ssh_port} (${h(s.ssh_user)}) ${s.tags?' | '+h(s.tags):''}</div>
                        ${!st.online&&st.error?`<div style="color:var(--danger);font-size:11px;margin-top:4px">${h(st.error)}</div>`:''}
                        ${st.last_seen?`<div class="text-muted" style="font-size:10px">Last seen: ${fmtAgo(st.last_seen)}</div>`:''}
                    </div>
                    <div class="container-stats">
                        <div class="container-stat"><div class="container-stat-val">${cpu}</div><div class="container-stat-label">CPU</div></div>
                        <div class="container-stat"><div class="container-stat-val">${mem}</div><div class="container-stat-label">MEM</div></div>
                        <div class="container-stat"><div class="container-stat-val">${disk}</div><div class="container-stat-label">DISK</div></div>
                        <div class="container-stat"><div class="container-stat-val">${load}</div><div class="container-stat-label">LOAD</div></div>
                    </div>
                    <div class="container-actions">
                        <button class="btn-small" onclick="VPSMon.probeServer(${s.id})">Probe</button>
                        <button class="btn-small" onclick="VPSMon.testServer(${s.id})">Test</button>
                        <button class="btn-small btn-danger" onclick="VPSMon.deleteServer(${s.id},'${h(s.name)}')">Delete</button>
                    </div>
                </div>`;
            }).join('');
        } catch(e) { console.error(e); }
    }

    // --- NOTIFICATIONS ---
    async function loadNotifications() {
        try {
            const d = await api('notifications/channels');
            const el = $('#notifications-list');
            const chs = d.channels||[];
            if (!chs.length) { el.innerHTML='<p class="text-muted pad-16">No channels configured. Add one to receive alerts.</p>'; return; }
            el.innerHTML = chs.map(c => `<div class="alert-item">
                <span class="alert-item-icon">&#9993;</span>
                <div class="alert-item-content">
                    <div class="alert-item-msg">${h(c.name)} <span class="badge badge-info">${h(c.kind)}</span></div>
                    <div class="alert-item-time">min severity: ${h(c.min_severity)} | ${c.enabled?'enabled':'disabled'}</div>
                </div>
                <div class="alert-item-actions">
                    <button class="btn-small" onclick="VPSMon.testNotif(${c.id})">Test</button>
                    <button class="btn-small btn-danger" onclick="VPSMon.deleteNotif(${c.id})">Delete</button>
                </div>
            </div>`).join('');
        } catch(e) { console.error(e); }
    }

    function renderNotifConfigFields(kind) {
        const el = $('#notif-config-fields');
        const field = (name, label, placeholder, type='text') =>
            `<div class="input-group"><label>${label}</label><input name="${name}" type="${type}" class="input-text" placeholder="${placeholder||''}"></div>`;
        if (kind === 'slack' || kind === 'discord') el.innerHTML = field('webhook_url','Webhook URL','https://hooks.slack.com/...');
        else if (kind === 'telegram') el.innerHTML = field('bot_token','Bot Token','123:ABC')+field('chat_id','Chat ID','@channel or numeric');
        else if (kind === 'webhook') el.innerHTML = field('url','URL','https://your.endpoint/hook');
        else if (kind === 'email') el.innerHTML = field('smtp_host','SMTP Host','smtp.gmail.com')+field('smtp_port','SMTP Port','587','number')+field('smtp_user','SMTP User','')+field('smtp_password','SMTP Password','','password')+field('from_addr','From','vpsmon@example.com')+field('to_addr','To','ops@example.com');
    }

    // --- AUDIT ---
    async function loadAudit(hours=24) {
        try {
            const d = await api(`audit?hours=${hours}`);
            const tb = $('#audit-tbody');
            tb.innerHTML = (d.events||[]).map(e => `<tr>
                <td>${fmtDT(e.ts)}</td><td>${h(e.username)}</td><td>${h(e.action)}</td>
                <td>${h(e.resource||'')}</td><td>${h(e.ip||'')}</td><td>${h(e.details||'')}</td>
            </tr>`).join('');
        } catch(e) { console.error(e); }
    }

    // --- 2FA ---
    async function loadTfaStatus() {
        try {
            const d = await api('auth/check');
            // Simple display — in real we'd have a dedicated endpoint
            const el = $('#tfa-status');
            el.innerHTML = `<button class="btn-primary" onclick="VPSMon.setup2FA()">Enable 2FA</button>
                <button class="btn-small btn-danger" onclick="VPSMon.disable2FA()" style="margin-left:8px">Disable</button>`;
        } catch {}
    }

    // --- USERS ---
    async function loadUsers() {
        try {
            const d = await api('users');
            const el = $('#users-list');
            el.innerHTML = '<div style="margin-top:8px"><div class="text-muted" style="font-size:12px;margin-bottom:4px">Existing users:</div>' +
                (d.users||[]).map(u => `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px">
                    <span>${h(u.username)}</span>
                    <button class="btn-small btn-danger" onclick="VPSMon.deleteUser(${u.id},'${h(u.username)}')">Delete</button>
                </div>`).join('') + '</div>';
        } catch(e) { console.error(e); }
    }

    // QR code — tiny inline generator (simple URL to QR service as fallback, or draw ourselves)
    function renderQR(text, container) {
        container.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(text)}" alt="2FA QR" style="border:4px solid white;border-radius:8px">`;
    }

    // --- Extend VPSMon global ---
    const existingVPSMon = window.VPSMon || {};
    Object.assign(window.VPSMon, {
        closeModal: id => $('#'+(id||'shortcuts-modal'))?.classList.add('hidden'),
        showAddServer: () => { $('#add-server-form').reset(); $('#add-server-modal').classList.remove('hidden'); },
        showAddNotif: () => { $('#add-notif-form').reset(); renderNotifConfigFields($('#notif-kind').value); $('#add-notif-modal').classList.remove('hidden'); },
        async probeServer(id) {
            const toastEl = (msg,type)=>{const t=document.createElement('div');t.className='toast toast-'+type;t.textContent=msg;$('#toast-container').appendChild(t);setTimeout(()=>t.remove(),3000);};
            toastEl('Probing…','info');
            await api(`servers/${id}/probe`, {method:'POST'});
            loadServers();
        },
        async testServer(id) {
            const r = await api(`servers/${id}/test`, {method:'POST'});
            const t = document.createElement('div');
            t.className = 'toast ' + (r.ok ? 'toast-success' : 'toast-error');
            t.textContent = r.ok ? `✓ Connected — ${r.hostname}` : `✗ ${r.error}`;
            $('#toast-container').appendChild(t);
            setTimeout(()=>t.remove(),5000);
        },
        async deleteServer(id, name) {
            if (!confirm(`Delete server "${name}"?`)) return;
            await api(`servers/${id}`, {method:'DELETE'});
            loadServers();
        },
        async testNotif(id) {
            const r = await api(`notifications/channels/${id}/test`, {method:'POST'});
            const t = document.createElement('div');
            t.className = 'toast ' + (r.ok ? 'toast-success' : 'toast-error');
            t.textContent = r.ok ? 'Test notification sent!' : `Failed: ${r.error}`;
            $('#toast-container').appendChild(t);
            setTimeout(()=>t.remove(),5000);
        },
        async deleteNotif(id) {
            if (!confirm('Delete this channel?')) return;
            await api(`notifications/channels/${id}`, {method:'DELETE'});
            loadNotifications();
        },
        async setup2FA() {
            const r = await api('auth/2fa/setup', {method:'POST'});
            if (!r.otpauth_uri) { alert('2FA setup failed'); return; }
            $('#tfa-setup').classList.remove('hidden');
            $('#tfa-secret').textContent = r.secret;
            renderQR(r.otpauth_uri, $('#tfa-qr'));
        },
        async disable2FA() {
            if (!confirm('Disable 2FA?')) return;
            await api('auth/2fa/disable', {method:'POST'});
            $('#tfa-setup').classList.add('hidden');
            alert('2FA disabled');
        },
        async deleteUser(id, name) {
            if (!confirm(`Delete user "${name}"?`)) return;
            const r = await api(`users/${id}`, {method:'DELETE'});
            if (r.error) alert(r.error);
            loadUsers();
        },
    });

    // --- Wire up forms ---
    document.addEventListener('DOMContentLoaded', () => {
        // Add server form
        $('#add-server-form')?.addEventListener('submit', async e => {
            e.preventDefault();
            const data = {
                name: $('#srv-name').value,
                hostname: $('#srv-host').value,
                ssh_user: $('#srv-user').value,
                ssh_port: parseInt($('#srv-port').value),
                ssh_key_path: $('#srv-key').value,
                ssh_password: $('#srv-pass').value,
                tags: $('#srv-tags').value,
            };
            const r = await api('servers', {method:'POST', body:data});
            if (r.ok) { $('#add-server-modal').classList.add('hidden'); loadServers(); }
            else alert(r.error || 'Failed');
        });

        // Notif kind selector
        $('#notif-kind')?.addEventListener('change', e => renderNotifConfigFields(e.target.value));

        // Add notif form
        $('#add-notif-form')?.addEventListener('submit', async e => {
            e.preventDefault();
            const cfg = {};
            $('#notif-config-fields').querySelectorAll('input').forEach(i => { if (i.value) cfg[i.name] = i.value; });
            const data = {
                name: $('#notif-name').value,
                kind: $('#notif-kind').value,
                config: cfg,
                min_severity: $('#notif-severity').value,
            };
            const r = await api('notifications/channels', {method:'POST', body:data});
            if (r.ok) { $('#add-notif-modal').classList.add('hidden'); loadNotifications(); }
            else alert(r.error || 'Failed');
        });

        // TFA verify form
        $('#tfa-verify-form')?.addEventListener('submit', async e => {
            e.preventDefault();
            const r = await api('auth/2fa/verify', {method:'POST', body:{code: $('#tfa-code').value}});
            if (r.ok) { alert('2FA enabled!'); $('#tfa-setup').classList.add('hidden'); }
            else alert(r.error || 'Invalid code');
        });

        // Add user form
        $('#add-user-form')?.addEventListener('submit', async e => {
            e.preventDefault();
            const r = await api('users', {method:'POST', body:{
                username: $('#new-username').value,
                password: $('#new-user-password').value,
            }});
            if (r.ok) { $('#new-username').value=''; $('#new-user-password').value=''; loadUsers(); }
            else alert(r.error || 'Failed');
        });

        // Audit time range
        $$('#audit-time-range .time-btn').forEach(b => b.addEventListener('click', () => {
            $$('#audit-time-range .time-btn').forEach(x=>x.classList.remove('active'));
            b.classList.add('active');
            loadAudit(parseInt(b.dataset.range));
        }));

        // Hook into hash navigation to load page data
        window.addEventListener('hashchange', () => {
            const page = window.location.hash.slice(1);
            if (page === 'servers') loadServers();
            if (page === 'notifications') loadNotifications();
            if (page === 'audit') loadAudit();
            if (page === 'settings') { loadTfaStatus(); loadUsers(); }
        });

        // Load initial if on those pages
        const p = window.location.hash.slice(1);
        if (p === 'servers') loadServers();
        else if (p === 'notifications') loadNotifications();
        else if (p === 'audit') loadAudit();
        else if (p === 'settings') { loadTfaStatus(); loadUsers(); }

        // Auto-refresh servers page every 10s when visible
        setInterval(() => { if (window.location.hash === '#servers') loadServers(); }, 10000);
    });
})();
