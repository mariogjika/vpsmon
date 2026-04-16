// VPSMon — AdvancedChart with zoom, pan, multi-series comparison, crosshair, PNG export
// Keyboard: +/- zoom, arrows pan, 0 reset, e export
(function () {
  'use strict';

  const COLORS = ['#6366f1','#ec4899','#22c55e','#f59e0b','#06b6d4','#ef4444','#8b5cf6','#14b8a6'];

  class AdvancedChart {
    /**
     * @param {HTMLElement|string} container
     * @param {Object} opts
     *   series: [{name, color, suffix, formatFn, data:[{ts, value}]}]
     *   showLegend, showGrid, showCrosshair, yMin, yMax
     */
    constructor(container, opts = {}) {
      this.container = typeof container === 'string' ? document.querySelector(container) : container;
      if (!this.container) return;
      this.opts = Object.assign({ showLegend: true, showGrid: true, showCrosshair: true }, opts);
      this.series = opts.series || [];
      this.visible = new Set(this.series.map((_, i) => i));
      this.view = null; // [tsMin, tsMax] when zoomed/panned; null = full
      this.hover = null;
      this.dragStart = null;
      this.dragRect = null;

      this.container.innerHTML = '';
      this.container.classList.add('advchart');
      this.canvas = document.createElement('canvas');
      this.canvas.tabIndex = 0;
      this.container.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d');

      this.toolbar = document.createElement('div');
      this.toolbar.className = 'advchart-toolbar';
      this.toolbar.innerHTML = `
        <button data-act="zoomout" title="Zoom out (−)">−</button>
        <button data-act="reset"   title="Reset view (0)">Reset</button>
        <button data-act="export"  title="Export PNG (E)">PNG</button>
        <button data-act="csv"     title="Export CSV">CSV</button>
        <span class="advchart-range"></span>
      `;
      this.container.appendChild(this.toolbar);
      this.rangeLabel = this.toolbar.querySelector('.advchart-range');

      this._bindEvents();
      this._resize();
      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this.container);
    }

    setSeries(series) {
      this.series = series;
      this.visible = new Set(series.map((_, i) => i));
      this.view = null;
      this.draw();
    }

    updateSeries(idx, data) {
      if (this.series[idx]) {
        this.series[idx].data = data;
        this.draw();
      }
    }

    _bindEvents() {
      const c = this.canvas;
      c.addEventListener('wheel', (e) => {
        e.preventDefault();
        this._zoomAt(e.offsetX, e.deltaY < 0 ? 0.8 : 1.25);
      }, { passive: false });

      c.addEventListener('mousedown', (e) => {
        if (e.shiftKey || e.button === 2) {
          this.dragStart = { type: 'pan', x: e.offsetX, view: this.view ? [...this.view] : this._fullRange() };
        } else {
          this.dragStart = { type: 'zoom', x: e.offsetX };
        }
      });
      c.addEventListener('mousemove', (e) => {
        this.hover = { x: e.offsetX, y: e.offsetY };
        if (this.dragStart) {
          if (this.dragStart.type === 'zoom') {
            this.dragRect = { x1: this.dragStart.x, x2: e.offsetX };
          } else if (this.dragStart.type === 'pan') {
            const dx = e.offsetX - this.dragStart.x;
            const p = this._padding(), cw = this.w - p.left - p.right;
            const [a, b] = this.dragStart.view;
            const shift = -(dx / cw) * (b - a);
            this.view = [a + shift, b + shift];
          }
        }
        this.draw();
      });
      c.addEventListener('mouseup', (e) => {
        if (this.dragStart && this.dragStart.type === 'zoom' && this.dragRect) {
          const { x1, x2 } = this.dragRect;
          if (Math.abs(x2 - x1) > 10) {
            const p = this._padding(), cw = this.w - p.left - p.right;
            const [a, b] = this.view || this._fullRange();
            const range = b - a;
            const newA = a + ((Math.min(x1, x2) - p.left) / cw) * range;
            const newB = a + ((Math.max(x1, x2) - p.left) / cw) * range;
            this.view = [newA, newB];
          }
        }
        this.dragStart = null;
        this.dragRect = null;
        this.draw();
      });
      c.addEventListener('mouseleave', () => {
        this.hover = null; this.dragStart = null; this.dragRect = null; this.draw();
      });
      c.addEventListener('dblclick', () => { this.view = null; this.draw(); });
      c.addEventListener('contextmenu', (e) => e.preventDefault());

      c.addEventListener('keydown', (e) => {
        if (e.key === '+' || e.key === '=') { this._zoomAt(this.w / 2, 0.8); e.preventDefault(); }
        else if (e.key === '-') { this._zoomAt(this.w / 2, 1.25); e.preventDefault(); }
        else if (e.key === '0') { this.view = null; this.draw(); e.preventDefault(); }
        else if (e.key === 'ArrowLeft')  { this._pan(-0.1); e.preventDefault(); }
        else if (e.key === 'ArrowRight') { this._pan( 0.1); e.preventDefault(); }
        else if (e.key.toLowerCase() === 'e') { this.exportPNG(); e.preventDefault(); }
      });

      this.toolbar.addEventListener('click', (e) => {
        const act = e.target.dataset.act;
        if (act === 'zoomout') this._zoomAt(this.w / 2, 1.5);
        else if (act === 'reset') { this.view = null; this.draw(); }
        else if (act === 'export') this.exportPNG();
        else if (act === 'csv') this.exportCSV();
      });
    }

    _zoomAt(px, factor) {
      const p = this._padding(), cw = this.w - p.left - p.right;
      const [a, b] = this.view || this._fullRange();
      const ratio = (px - p.left) / cw;
      const center = a + ratio * (b - a);
      const range = (b - a) * factor;
      this.view = [center - range * ratio, center + range * (1 - ratio)];
      this.draw();
    }
    _pan(frac) {
      const [a, b] = this.view || this._fullRange();
      const d = (b - a) * frac;
      this.view = [a + d, b + d];
      this.draw();
    }

    _fullRange() {
      let min = Infinity, max = -Infinity;
      for (const s of this.series) {
        for (const d of (s.data || [])) {
          if (d.ts < min) min = d.ts;
          if (d.ts > max) max = d.ts;
        }
      }
      if (!isFinite(min)) return [Date.now() / 1000 - 3600, Date.now() / 1000];
      return [min, max];
    }

    _padding() { return { top: 22, right: 14, bottom: 28, left: 58 }; }

    _resize() {
      const r = this.container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.canvas.width = r.width * dpr;
      this.canvas.height = r.height * dpr;
      this.canvas.style.width = r.width + 'px';
      this.canvas.style.height = r.height + 'px';
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.scale(dpr, dpr);
      this.w = r.width; this.h = r.height;
      this.draw();
    }

    draw() {
      const ctx = this.ctx, w = this.w, h = this.h;
      if (!w || !h) return;
      ctx.clearRect(0, 0, w, h);

      const activeSeries = this.series.filter((_, i) => this.visible.has(i));
      if (!activeSeries.length || !activeSeries.some(s => s.data && s.data.length)) {
        ctx.fillStyle = '#5c6078';
        ctx.font = '12px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('no data', w / 2, h / 2);
        return;
      }

      const p = this._padding();
      const cw = w - p.left - p.right, ch = h - p.top - p.bottom;
      const [tsMin, tsMax] = this.view || this._fullRange();
      const tsRange = tsMax - tsMin || 1;

      let vMin = Infinity, vMax = -Infinity;
      for (const s of activeSeries) {
        for (const d of s.data) {
          if (d.ts >= tsMin && d.ts <= tsMax) {
            if (d.value < vMin) vMin = d.value;
            if (d.value > vMax) vMax = d.value;
          }
        }
      }
      if (!isFinite(vMin)) { vMin = 0; vMax = 1; }
      if (vMin === vMax) { vMin = vMin - 1; vMax = vMax + 1; }
      if (this.opts.yMin != null) vMin = this.opts.yMin;
      if (this.opts.yMax != null) vMax = this.opts.yMax;
      const vRange = vMax - vMin;

      // Grid
      if (this.opts.showGrid) {
        ctx.strokeStyle = 'rgba(42,45,58,0.6)';
        ctx.lineWidth = 0.5;
        ctx.fillStyle = '#5c6078';
        ctx.font = '10px -apple-system, sans-serif';
        ctx.textAlign = 'right';
        for (let i = 0; i <= 4; i++) {
          const y = p.top + (ch / 4) * i;
          ctx.beginPath(); ctx.moveTo(p.left, y); ctx.lineTo(w - p.right, y); ctx.stroke();
          const v = vMax - (vRange / 4) * i;
          const s0 = activeSeries[0];
          const lbl = s0 && s0.formatFn ? s0.formatFn(v) : v.toFixed(1) + (s0 && s0.suffix || '');
          ctx.fillText(lbl, p.left - 6, y + 3);
        }
        ctx.textAlign = 'center';
        for (let i = 0; i <= 5; i++) {
          const x = p.left + (cw * i / 5);
          const t = tsMin + (tsRange * i / 5);
          const d = new Date(t * 1000);
          const lbl = tsRange > 86400 * 3
            ? (d.getMonth() + 1) + '/' + d.getDate()
            : d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
          ctx.fillText(lbl, x, h - 8);
        }
      }

      // Lines
      activeSeries.forEach((s, iOrig) => {
        const color = s.color || COLORS[iOrig % COLORS.length];
        ctx.strokeStyle = color;
        ctx.fillStyle = color + '22';
        ctx.lineWidth = 1.5;
        ctx.lineJoin = 'round';
        ctx.beginPath();
        let started = false, pts = [];
        for (const d of s.data) {
          if (d.ts < tsMin || d.ts > tsMax) continue;
          const x = p.left + ((d.ts - tsMin) / tsRange) * cw;
          const y = p.top + ch - ((d.value - vMin) / vRange) * ch;
          pts.push([x, y]);
          started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
        }
        ctx.stroke();
        if (activeSeries.length === 1 && pts.length) {
          ctx.lineTo(pts[pts.length - 1][0], p.top + ch);
          ctx.lineTo(pts[0][0], p.top + ch);
          ctx.closePath();
          ctx.fill();
        }
      });

      // Crosshair
      if (this.opts.showCrosshair && this.hover && this.hover.x >= p.left && this.hover.x <= w - p.right && !this.dragStart) {
        ctx.strokeStyle = 'rgba(148,163,184,0.5)';
        ctx.lineWidth = 0.5;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(this.hover.x, p.top);
        ctx.lineTo(this.hover.x, p.top + ch);
        ctx.stroke();
        ctx.setLineDash([]);

        const t = tsMin + ((this.hover.x - p.left) / cw) * tsRange;
        const tooltip = [new Date(t * 1000).toLocaleTimeString()];
        activeSeries.forEach((s, iOrig) => {
          const nearest = (s.data || []).reduce((best, d) =>
            !best || Math.abs(d.ts - t) < Math.abs(best.ts - t) ? d : best, null);
          if (nearest) {
            const lbl = s.formatFn ? s.formatFn(nearest.value) : nearest.value.toFixed(2) + (s.suffix || '');
            tooltip.push(`${s.name}: ${lbl}`);
          }
        });

        // Tooltip box
        ctx.font = '11px -apple-system, sans-serif';
        const lines = tooltip;
        const lh = 14;
        const tw = Math.max(...lines.map(l => ctx.measureText(l).width)) + 16;
        const th = lines.length * lh + 8;
        let tx = this.hover.x + 10;
        if (tx + tw > w - p.right) tx = this.hover.x - tw - 10;
        const ty = Math.min(this.hover.y, h - th - 4);
        ctx.fillStyle = 'rgba(20,22,30,0.95)';
        ctx.strokeStyle = 'rgba(148,163,184,0.3)';
        ctx.beginPath(); ctx.roundRect(tx, ty, tw, th, 4); ctx.fill(); ctx.stroke();
        ctx.textAlign = 'left';
        lines.forEach((l, i) => {
          ctx.fillStyle = i === 0 ? '#94a3b8' : (activeSeries[i - 1]?.color || COLORS[(i - 1) % COLORS.length]);
          ctx.fillText(l, tx + 8, ty + 14 + i * lh);
        });
      }

      // Drag-zoom selection
      if (this.dragRect) {
        const x1 = Math.min(this.dragRect.x1, this.dragRect.x2);
        const x2 = Math.max(this.dragRect.x1, this.dragRect.x2);
        ctx.fillStyle = 'rgba(99,102,241,0.15)';
        ctx.strokeStyle = 'rgba(99,102,241,0.6)';
        ctx.fillRect(x1, p.top, x2 - x1, ch);
        ctx.strokeRect(x1, p.top, x2 - x1, ch);
      }

      // Legend
      if (this.opts.showLegend) {
        let lx = p.left;
        ctx.font = '11px -apple-system, sans-serif';
        ctx.textAlign = 'left';
        this.series.forEach((s, i) => {
          const color = s.color || COLORS[i % COLORS.length];
          const active = this.visible.has(i);
          const lbl = s.name || `Series ${i + 1}`;
          const tw = ctx.measureText(lbl).width + 20;
          ctx.globalAlpha = active ? 1 : 0.4;
          ctx.fillStyle = color;
          ctx.fillRect(lx, 8, 10, 10);
          ctx.fillStyle = '#cbd5e1';
          ctx.fillText(lbl, lx + 14, 17);
          ctx.globalAlpha = 1;
          lx += tw + 8;
        });
      }

      // Range label
      if (this.rangeLabel) {
        const fmt = (t) => new Date(t * 1000).toLocaleString();
        this.rangeLabel.textContent = `${fmt(tsMin)} → ${fmt(tsMax)}`;
      }
    }

    toggleSeries(idx) {
      if (this.visible.has(idx)) this.visible.delete(idx);
      else this.visible.add(idx);
      this.draw();
    }

    exportPNG() {
      const link = document.createElement('a');
      link.download = `vpsmon-chart-${Date.now()}.png`;
      link.href = this.canvas.toDataURL('image/png');
      link.click();
    }

    exportCSV() {
      if (!this.series.length) return;
      const all = new Map();
      this.series.forEach((s, i) => {
        (s.data || []).forEach(d => {
          if (!all.has(d.ts)) all.set(d.ts, {});
          all.get(d.ts)[i] = d.value;
        });
      });
      const rows = [['timestamp', 'iso', ...this.series.map(s => s.name || 'value')]];
      [...all.keys()].sort().forEach(ts => {
        const row = [ts, new Date(ts * 1000).toISOString()];
        this.series.forEach((_, i) => row.push(all.get(ts)[i] ?? ''));
        rows.push(row);
      });
      const csv = rows.map(r => r.join(',')).join('\n');
      const blob = new Blob([csv], { type: 'text/csv' });
      const link = document.createElement('a');
      link.download = `vpsmon-chart-${Date.now()}.csv`;
      link.href = URL.createObjectURL(blob);
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    destroy() { if (this._ro) this._ro.disconnect(); }
  }

  // Custom dashboard widget helper
  class DashboardLayout {
    constructor(container, key = 'vpsmon-dash-layout') {
      this.container = typeof container === 'string' ? document.querySelector(container) : container;
      this.key = key;
      this.widgets = [];
    }
    addWidget(def) {
      this.widgets.push(def);
      this.render();
      this.save();
    }
    removeWidget(id) {
      this.widgets = this.widgets.filter(w => w.id !== id);
      this.render();
      this.save();
    }
    save() { try { localStorage.setItem(this.key, JSON.stringify(this.widgets)); } catch {} }
    load() {
      try { this.widgets = JSON.parse(localStorage.getItem(this.key) || '[]'); } catch { this.widgets = []; }
      this.render();
    }
    render() {
      if (!this.container) return;
      this.container.innerHTML = '';
      this.widgets.forEach(w => {
        const el = document.createElement('div');
        el.className = 'dash-widget';
        el.innerHTML = `
          <div class="dash-widget-header">
            <span class="dash-widget-title">${w.title || ''}</span>
            <button class="dash-widget-remove" data-id="${w.id}">×</button>
          </div>
          <div class="dash-widget-body" id="widget-body-${w.id}"></div>`;
        this.container.appendChild(el);
        el.querySelector('.dash-widget-remove').addEventListener('click', () => this.removeWidget(w.id));
      });
    }
  }

  window.AdvancedChart = AdvancedChart;
  window.DashboardLayout = DashboardLayout;
})();
