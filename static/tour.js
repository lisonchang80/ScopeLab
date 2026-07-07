/*!
 * WebTour — dependency-free spotlight onboarding tour (single file, self-styled).
 *
 * Usage (ES module):
 *   import WebTour from './tour.js';
 *   WebTour.autoStartOnce(steps, { key: 'myapp', version: 1 });
 *   WebTour.attachHelpButton(steps, { key: 'myapp' });
 *
 * Classic <script> tag: load with type="module", or delete the `export` lines at the
 * bottom and use the `window.WebTour` global.
 *
 * Step schema:
 *   {
 *     selector?: string,        // CSS selector; omit → centered card, no spotlight
 *     route?: string,           // SPA route; needs opts.navigate(route) to be passed
 *     title: string,
 *     body?: string,            // plain text
 *     html?: string,            // trusted HTML (project-authored only), overrides body
 *     img?: string,             // image URL shown in the card (screenshot steps)
 *     imgAlt?: string,
 *     placement?: 'top'|'bottom'|'left'|'right',  // default: auto
 *     interactive?: boolean,    // true → clicks pass through the spotlight hole
 *     timeout?: number          // ms to wait for selector (default 4000)
 *   }
 *
 * Options:
 *   key, version               // localStorage seen-flag namespace (bump version to re-show)
 *   navigate(route)            // SPA navigation adapter (e.g. react-router navigate)
 *   labels                     // { next, prev, done, skip, help, stepOf }
 *   accent                     // CSS color, default #3b82f6
 *   padding, radius            // spotlight hole padding / corner radius (px)
 *   overlayOpacity             // 0..1, default 0.62
 *   zIndex                     // default 100000000
 *   onFinish(), onSkip(index)  // analytics hooks
 */

const DEFAULT_LABELS = {
  next: '下一步',
  prev: '上一步',
  done: '完成',
  skip: '關閉導覽',
  help: '功能導覽',
  stepOf: (i, n) => `${i} / ${n}`,
};

const DEFAULTS = {
  key: 'tour',
  version: 1,
  padding: 8,
  radius: 10,
  overlayOpacity: 0.62,
  zIndex: 100000000, // high but far from INT_MAX — extreme values invite compositor quirks
  accent: '#3b82f6',
  navigate: null,
  labels: {},
  onFinish: null,
  onSkip: null,
};

const STYLE_ID = 'wt-style';
const reduced = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const lerp = (a, b, t) => a + (b - a) * t;

let active = null; // single active tour instance

/* ------------------------------------------------------------------ styles */

function injectStyles(opts) {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = `
.wt-root{position:fixed;inset:0;pointer-events:none;font:14px/1.6 system-ui,-apple-system,"Segoe UI","Noto Sans TC","PingFang TC",sans-serif}
.wt-shade{position:absolute;inset:0;pointer-events:auto;cursor:default;will-change:transform}
.wt-blocker{position:absolute;pointer-events:auto;background:transparent}
.wt-card{position:fixed;pointer-events:auto;box-sizing:border-box;max-width:340px;min-width:250px;
  background:#fff;color:#1b2430;border-radius:12px;padding:14px 16px 12px;
  box-shadow:0 12px 40px rgba(10,15,30,.30),0 2px 8px rgba(10,15,30,.16);outline:none}
.wt-card.wt-center{left:50%!important;top:50%!important;transform:translate(-50%,-50%)}
.wt-card.wt-enter{animation:wt-in .22s ease-out}
.wt-card.wt-pulse{animation:wt-pulse .3s ease-out}
@keyframes wt-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes wt-pulse{50%{transform:scale(1.02)}}
.wt-card.wt-center.wt-enter{animation:wt-in-c .22s ease-out}
@keyframes wt-in-c{from{opacity:0;transform:translate(-50%,calc(-50% + 6px))}to{opacity:1;transform:translate(-50%,-50%)}}
.wt-arrow{position:absolute;width:12px;height:12px;background:inherit;transform:rotate(45deg);box-shadow:none}
.wt-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px}
.wt-counter{font-size:11px;color:#8a94a6;letter-spacing:.03em}
.wt-close{border:0;background:none;padding:2px 4px;margin:-2px -6px 0 0;cursor:pointer;color:#8a94a6;
  font-size:14px;line-height:1;border-radius:6px}
.wt-close:hover{color:#1b2430;background:rgba(0,0,0,.06)}
.wt-title{margin:0 0 4px;font-size:15px;font-weight:600;line-height:1.4}
.wt-img{display:block;width:100%;max-height:180px;object-fit:cover;border-radius:8px;
  margin:6px 0 4px;border:1px solid rgba(0,0,0,.08)}
.wt-img[hidden]{display:none}
.wt-body{margin:0;color:#3d4757}
.wt-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}
.wt-dots{display:flex;gap:6px;align-items:center}
.wt-dot{width:6px;height:6px;border-radius:3px;border:0;padding:0;cursor:pointer;
  background:rgba(0,0,0,.18);transition:width .2s,background .2s}
.wt-dot[aria-current="true"]{width:16px;background:var(--wt-accent)}
.wt-btns{display:flex;gap:8px;flex:none}
.wt-btn{border-radius:8px;padding:5px 12px;font-size:13px;font-weight:500;cursor:pointer;line-height:1.5}
.wt-prev{border:1px solid rgba(0,0,0,.16);background:none;color:#3d4757}
.wt-prev:disabled{opacity:.4;cursor:default}
.wt-next{border:1px solid transparent;background:var(--wt-accent);color:#fff}
.wt-next:hover,.wt-prev:not(:disabled):hover{filter:brightness(.94)}
.wt-help{position:fixed;right:16px;bottom:16px;width:36px;height:36px;border-radius:50%;
  border:1px solid rgba(0,0,0,.15);background:rgba(255,255,255,.88);backdrop-filter:blur(4px);
  color:#556070;font:600 16px/1 system-ui,sans-serif;cursor:pointer;opacity:.6;
  transition:opacity .2s;box-shadow:0 2px 8px rgba(10,15,30,.12)}
.wt-help:hover{opacity:1}
@media (max-width:639px){
  .wt-card:not(.wt-center){left:12px!important;right:12px!important;top:auto!important;
    bottom:calc(12px + env(safe-area-inset-bottom))!important;max-width:none}
  .wt-arrow{display:none}
}
@media (prefers-color-scheme:dark){
  .wt-card{background:#242b36;color:#e8edf5}
  .wt-body{color:#b8c2d0}
  .wt-counter,.wt-close{color:#7f8a9b}
  .wt-close:hover{color:#e8edf5;background:rgba(255,255,255,.08)}
  .wt-dot{background:rgba(255,255,255,.22)}
  .wt-prev{border-color:rgba(255,255,255,.22);color:#b8c2d0}
  .wt-img{border-color:rgba(255,255,255,.1)}
  .wt-help{background:rgba(36,43,54,.88);border-color:rgba(255,255,255,.18);color:#b8c2d0}
}
@media (prefers-reduced-motion:reduce){
  .wt-card.wt-enter,.wt-card.wt-center.wt-enter,.wt-card.wt-pulse{animation:none}
  .wt-dot{transition:none}
}`;
  document.head.appendChild(s);
}

/* ------------------------------------------------------------------ helpers */

function holePath(vw, vh, r) {
  const outer = `M0 0H${vw}V${vh}H0Z`;
  if (!r || r.w <= 0 || r.h <= 0) return outer;
  const rad = Math.max(0, Math.min(r.rad, r.w / 2, r.h / 2));
  const { x, y, w, h } = r;
  return (
    outer +
    `M${x + rad} ${y}h${w - 2 * rad}a${rad} ${rad} 0 0 1 ${rad} ${rad}` +
    `v${h - 2 * rad}a${rad} ${rad} 0 0 1 ${-rad} ${rad}` +
    `h${-(w - 2 * rad)}a${rad} ${rad} 0 0 1 ${-rad} ${-rad}` +
    `v${-(h - 2 * rad)}a${rad} ${rad} 0 0 1 ${rad} ${-rad}Z`
  );
}

function paddedRect(el, pad, rad) {
  const b = el.getBoundingClientRect();
  return { x: b.left - pad, y: b.top - pad, w: b.width + 2 * pad, h: b.height + 2 * pad, rad };
}

function waitFor(selector, timeout) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const tick = () => {
      const el = document.querySelector(selector);
      if (el && el.getClientRects().length > 0) return resolve(el);
      if (performance.now() - t0 > timeout) return resolve(null);
      setTimeout(tick, 120);
    };
    tick();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ tour */

class Tour {
  constructor(steps, opts) {
    this.steps = steps;
    this.opts = { ...DEFAULTS, ...opts, labels: { ...DEFAULT_LABELS, ...(opts.labels || {}) } };
    this.i = -1;
    this.dir = 1;
    this.target = null;
    this.hole = null;
    this.holeShown = false;
    this.raf = 0;
    this.repositionRaf = 0;
    this.done = false;
    this.prevFocus = document.activeElement;
    this.build();
    this.bind();
  }

  build() {
    injectStyles(this.opts);
    const o = this.opts;
    this.root = document.createElement('div');
    this.root.className = 'wt-root';
    this.root.style.zIndex = o.zIndex;
    this.root.style.setProperty('--wt-accent', o.accent);

    // Spotlight shade: one div doing dim + hole. Primary = clip-path:path(evenodd)
    // (semi-transparent SVG paint is dropped by some embedded webviews, so no SVG here);
    // fallback = hole-sized div with a huge box-shadow.
    // NOTE: .wt-shade carries will-change:transform on purpose — without its own
    // compositor layer, Chromium culls the bottom-most translucent paint chunk that
    // exactly covers this fixed stacking context, silently blanking the whole shade
    // (z-index/DOM-order/1px-inset tweaks don't help). Don't remove it.
    this.clipMode = typeof CSS !== 'undefined' && CSS.supports &&
      CSS.supports('clip-path', 'path(evenodd, "M0 0H10V10H0Z")');
    this.shadeColor = `rgba(8,12,20,${o.overlayOpacity})`;
    this.shade = document.createElement('div');
    this.shade.className = 'wt-shade';
    this.shade.style.background = this.shadeColor;

    this.blocker = document.createElement('div');
    this.blocker.className = 'wt-blocker';
    this.blocker.style.display = 'none';

    this.card = document.createElement('div');
    this.card.className = 'wt-card';
    this.card.setAttribute('role', 'dialog');
    this.card.setAttribute('aria-modal', 'true');
    this.card.tabIndex = -1;
    this.card.innerHTML = `
      <div class="wt-arrow" hidden></div>
      <div class="wt-head"><span class="wt-counter"></span>
        <button type="button" class="wt-close" aria-label="${o.labels.skip}">✕</button></div>
      <h3 class="wt-title" id="wt-title"></h3>
      <img class="wt-img" alt="" hidden>
      <p class="wt-body"></p>
      <div class="wt-foot"><div class="wt-dots"></div>
        <div class="wt-btns">
          <button type="button" class="wt-btn wt-prev">${o.labels.prev}</button>
          <button type="button" class="wt-btn wt-next">${o.labels.next}</button>
        </div></div>`;
    this.card.setAttribute('aria-labelledby', 'wt-title');
    this.$ = (sel) => this.card.querySelector(sel);

    const dots = this.$('.wt-dots');
    this.steps.forEach((_, j) => {
      const d = document.createElement('button');
      d.type = 'button';
      d.className = 'wt-dot';
      d.setAttribute('aria-label', `第 ${j + 1} 步`);
      d.addEventListener('click', () => this.show(j));
      dots.appendChild(d);
    });

    this.root.append(this.shade, this.blocker, this.card);
    // Hidden until the first show(): if the overlay is painted in the page's very
    // first frame, Chromium's initial layerization may cull the full-cover
    // translucent shade entirely (observed on Windows Chrome, dsf 1.5) — and a
    // healthy browser would flash the un-holed shade. start() defers show(0) past
    // the first frame; this keeps the interim invisible.
    this.root.style.visibility = 'hidden';
    document.body.appendChild(this.root);
  }

  bind() {
    this.$('.wt-close').addEventListener('click', () => this.skip());
    this.$('.wt-prev').addEventListener('click', () => this.prev());
    this.$('.wt-next').addEventListener('click', () => this.next());
    const pulse = () => {
      this.card.classList.remove('wt-pulse');
      void this.card.offsetWidth; // restart animation
      this.card.classList.add('wt-pulse');
    };
    this.shade.addEventListener('click', pulse);
    this.blocker.addEventListener('click', pulse);
    this.$('.wt-img').addEventListener('load', () => this.schedule());

    this.onKey = (e) => {
      if (this.done) return;
      const t = e.target;
      const typing =
        t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ''));
      if (e.key === 'Escape') { e.preventDefault(); this.skip(); return; }
      if (typing) return;
      if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); this.next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); this.prev(); }
      else if (e.key === 'Tab' && !this.steps[this.i]?.interactive) {
        const f = [...this.card.querySelectorAll('button:not([disabled])')];
        if (!f.length) return;
        const idx = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(idx + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
      }
    };
    this.onSchedule = () => this.schedule();
    document.addEventListener('keydown', this.onKey, true);
    addEventListener('resize', this.onSchedule);
    addEventListener('scroll', this.onSchedule, true);
    this.ro = typeof ResizeObserver === 'function' ? new ResizeObserver(this.onSchedule) : null;
    if (this.ro) this.ro.observe(this.card);
  }

  async show(i) {
    if (this.done) return;
    if (i < 0) return;
    if (i >= this.steps.length) return this.finish();
    this.dir = i >= this.i ? 1 : -1;
    this.i = i;
    const step = this.steps[i];
    const seq = (this.seq = (this.seq || 0) + 1); // guard against overlapping async shows

    if (step.route && this.opts.navigate) this.opts.navigate(step.route);

    let target = null;
    if (step.selector) {
      target = await waitFor(step.selector, step.timeout ?? 4000);
      if (seq !== this.seq || this.done) return;
      if (!target) {
        console.warn(`[WebTour] step ${i + 1}: selector not found, skipping —`, step.selector);
        return this.show(i + this.dir);
      }
    }
    if (this.ro && this.target) this.ro.unobserve(this.target);
    this.target = target;
    if (this.ro && target) this.ro.observe(target);

    if (target) {
      const b = target.getBoundingClientRect();
      const out = b.top < 70 || b.bottom > innerHeight - 70 || b.left < 0 || b.right > innerWidth;
      if (out) {
        target.scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' });
        await sleep(reduced() ? 30 : 380);
        if (seq !== this.seq || this.done) return;
      }
    }

    this.root.style.visibility = '';
    this.renderCard(step, i);
    this.update(true);
    this.card.classList.remove('wt-enter');
    void this.card.offsetWidth;
    this.card.classList.add('wt-enter');
    this.card.focus({ preventScroll: true });
  }

  renderCard(step, i) {
    const o = this.opts, n = this.steps.length;
    this.$('.wt-counter').textContent = o.labels.stepOf(i + 1, n);
    this.$('.wt-title').textContent = step.title || '';
    const body = this.$('.wt-body');
    if (step.html) body.innerHTML = step.html;
    else body.textContent = step.body || '';
    body.hidden = !step.html && !step.body;
    const img = this.$('.wt-img');
    if (step.img) { img.src = step.img; img.alt = step.imgAlt || step.title || ''; img.hidden = false; }
    else { img.hidden = true; img.removeAttribute('src'); }
    this.$('.wt-prev').disabled = i === 0;
    this.$('.wt-next').textContent = i === n - 1 ? o.labels.done : o.labels.next;
    this.card.querySelectorAll('.wt-dot').forEach((d, j) =>
      d.setAttribute('aria-current', j === i ? 'true' : 'false'));
  }

  /* Recompute hole + card position. animate=true only on step change. */
  update(animate) {
    if (this.done) return;
    const o = this.opts;
    let hole = null;
    if (this.target) {
      if (!this.target.isConnected) {
        const again = this.steps[this.i]?.selector &&
          document.querySelector(this.steps[this.i].selector);
        if (again) { this.target = again; if (this.ro) this.ro.observe(again); }
        else this.target = null;
      }
      if (this.target) hole = paddedRect(this.target, o.padding, o.radius);
    }
    this.applyHole(hole, animate);
    this.placeCard(hole);
    const interactive = !!this.steps[this.i]?.interactive;
    this.blocker.style.display = hole && !interactive ? '' : 'none';
    if (hole) {
      Object.assign(this.blocker.style, {
        left: hole.x + 'px', top: hole.y + 'px',
        width: hole.w + 'px', height: hole.h + 'px',
      });
    }
  }

  /* Paint the shade with a hole (r=null → full dim, no hole). */
  setHole(r) {
    const s = this.shade.style;
    if (this.clipMode) {
      // Clip via path() even when hole-less: an unclipped translucent chunk that
      // exactly covers the layer is culled by Chromium's layerization (blank shade).
      s.clipPath = `path(evenodd, "${holePath(innerWidth, innerHeight, r)}")`;
      return;
    }
    if (r) { // fallback: hole-sized div + huge shadow (dark area doesn't catch clicks)
      Object.assign(s, {
        inset: 'auto', left: r.x + 'px', top: r.y + 'px',
        width: r.w + 'px', height: r.h + 'px', borderRadius: (r.rad || 0) + 'px',
        background: 'transparent', pointerEvents: 'none',
        boxShadow: `0 0 0 200vmax ${this.shadeColor}`,
      });
    } else {
      Object.assign(s, {
        inset: '0', left: '', top: '', width: '', height: '', borderRadius: '0',
        background: this.shadeColor, pointerEvents: 'auto', boxShadow: 'none',
      });
    }
  }

  applyHole(to, animate) {
    const zero = to || { x: innerWidth / 2, y: innerHeight / 2, w: 0, h: 0, rad: 0 };
    if (this.raf) cancelAnimationFrame(this.raf);
    if (!animate || reduced() || !this.holeShown) {
      this.hole = zero;
      this.holeShown = true;
      this.setHole(to ? zero : null);
      return;
    }
    const from = this.hole, dest = zero, t0 = performance.now(), D = 280;
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / D);
      const e = 1 - Math.pow(1 - p, 3);
      const cur = {
        x: lerp(from.x, dest.x, e), y: lerp(from.y, dest.y, e),
        w: lerp(from.w, dest.w, e), h: lerp(from.h, dest.h, e),
        rad: lerp(from.rad || 0, dest.rad || 0, e),
      };
      this.hole = cur;
      this.setHole(cur.w > 0.5 ? cur : null);
      if (p < 1) this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  placeCard(hole) {
    const card = this.card, arrow = this.$('.wt-arrow');
    if (!hole) {
      card.classList.add('wt-center');
      card.style.left = card.style.top = '';
      arrow.hidden = true;
      return;
    }
    card.classList.remove('wt-center');
    if (innerWidth < 640) { // bottom sheet via CSS; clear inline pos
      card.style.left = card.style.top = '';
      arrow.hidden = true;
      return;
    }
    const vw = innerWidth, vh = innerHeight, m = 12, gap = 14;
    const cw = card.offsetWidth, ch = card.offsetHeight;
    const cx = hole.x + hole.w / 2, cy = hole.y + hole.h / 2;
    const pref = this.steps[this.i]?.placement;
    const order = [pref, 'bottom', 'top', 'right', 'left'].filter(
      (p, idx, a) => p && a.indexOf(p) === idx);
    let pos = null;
    for (const side of order) {
      let x, y;
      if (side === 'bottom') { y = hole.y + hole.h + gap; x = clamp(cx - cw / 2, m, vw - cw - m); if (y + ch <= vh - m) pos = { side, x, y }; }
      else if (side === 'top') { y = hole.y - gap - ch; x = clamp(cx - cw / 2, m, vw - cw - m); if (y >= m) pos = { side, x, y }; }
      else if (side === 'right') { x = hole.x + hole.w + gap; y = clamp(cy - ch / 2, m, vh - ch - m); if (x + cw <= vw - m) pos = { side, x, y }; }
      else if (side === 'left') { x = hole.x - gap - cw; y = clamp(cy - ch / 2, m, vh - ch - m); if (x >= m) pos = { side, x, y }; }
      if (pos) break;
    }
    if (!pos) pos = { side: 'bottom', x: clamp(cx - cw / 2, m, vw - cw - m), y: clamp(hole.y + hole.h + gap, m, vh - ch - m) };
    card.style.left = pos.x + 'px';
    card.style.top = pos.y + 'px';

    arrow.hidden = false;
    arrow.style.left = arrow.style.top = arrow.style.right = arrow.style.bottom = '';
    const ax = clamp(cx - pos.x - 6, 14, cw - 26);
    const ay = clamp(cy - pos.y - 6, 14, ch - 26);
    if (pos.side === 'bottom') { arrow.style.top = '-6px'; arrow.style.left = ax + 'px'; }
    else if (pos.side === 'top') { arrow.style.bottom = '-6px'; arrow.style.left = ax + 'px'; }
    else if (pos.side === 'right') { arrow.style.left = '-6px'; arrow.style.top = ay + 'px'; }
    else { arrow.style.right = '-6px'; arrow.style.top = ay + 'px'; }
  }

  schedule() {
    if (this.repositionRaf || this.done) return;
    this.repositionRaf = requestAnimationFrame(() => {
      this.repositionRaf = 0;
      this.update(false);
    });
  }

  next() { this.show(this.i + 1); }
  prev() { this.show(this.i - 1); }

  skip() {
    if (this.done) return;
    const at = this.i;
    this.cleanup();
    if (this.opts.onSkip) this.opts.onSkip(at);
  }

  finish() {
    if (this.done) return;
    this.cleanup();
    if (this.opts.onFinish) this.opts.onFinish();
  }

  cleanup() {
    this.done = true;
    this.seq = (this.seq || 0) + 1;
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.repositionRaf) cancelAnimationFrame(this.repositionRaf);
    document.removeEventListener('keydown', this.onKey, true);
    removeEventListener('resize', this.onSchedule);
    removeEventListener('scroll', this.onSchedule, true);
    if (this.ro) this.ro.disconnect();
    this.root.remove();
    if (active === this) active = null;
    if (this.prevFocus && this.prevFocus.focus) {
      try { this.prevFocus.focus({ preventScroll: true }); } catch { /* detached */ }
    }
  }
}

/* ------------------------------------------------------------------ API */

function seenKey(opts) {
  return `wt-seen:${opts.key || DEFAULTS.key}:v${opts.version || DEFAULTS.version}`;
}

/** Start the tour now. Returns a controller, or null if one is already running. */
function start(steps, opts = {}) {
  if (active || !steps || !steps.length || typeof document === 'undefined') return null;
  const tour = new Tour(steps, opts);
  active = tour;
  // Timing rules for the first show():
  // 1. Hidden tab → wait for visibility. A tour that autoplays in a background tab
  //    burns its one autostart unseen, AND Chromium's first layerization of a hidden
  //    tab culls the translucent shade (blank overlay until a forced repaint).
  // 2. Still loading → wait for load + settle, then re-check visibility.
  // 3. Visible & loaded (help button, SPA effect) → next frame, effectively instant.
  const go = () => { if (!tour.done && tour.i < 0) tour.show(0); };
  const kick = () => {
    if (tour.done) return;
    if (document.hidden) {
      const once = () => {
        if (document.hidden) return;
        document.removeEventListener('visibilitychange', once);
        requestAnimationFrame(() => requestAnimationFrame(go));
      };
      document.addEventListener('visibilitychange', once);
    } else if (document.readyState !== 'complete') {
      addEventListener('load', () => setTimeout(kick, 400), { once: true });
    } else {
      requestAnimationFrame(() => requestAnimationFrame(go));
    }
  };
  kick();
  return {
    next: () => tour.next(),
    prev: () => tour.prev(),
    goto: (i) => tour.show(i),
    stop: () => tour.skip(),
  };
}

/** Start only if this key+version hasn't been seen; marks seen on finish OR skip. */
function autoStartOnce(steps, opts = {}) {
  let seen = false;
  try { seen = !!localStorage.getItem(seenKey(opts)); } catch { /* storage blocked */ }
  if (seen) return null;
  const mark = () => { try { localStorage.setItem(seenKey(opts), String(Date.now())); } catch { /* ignore */ } };
  return start(steps, {
    ...opts,
    onFinish: () => { mark(); opts.onFinish && opts.onFinish(); },
    onSkip: (i) => { mark(); opts.onSkip && opts.onSkip(i); },
  });
}

/** Floating "?" button (bottom-right) that (re)starts the tour on demand. */
function attachHelpButton(steps, opts = {}) {
  if (typeof document === 'undefined') return null;
  const id = `wt-help-${opts.key || DEFAULTS.key}`;
  let btn = document.getElementById(id);
  if (btn) return btn;
  injectStyles(opts);
  btn = document.createElement('button');
  btn.type = 'button';
  btn.id = id;
  btn.className = 'wt-help';
  btn.textContent = '?';
  btn.title = (opts.labels && opts.labels.help) || DEFAULT_LABELS.help;
  btn.setAttribute('aria-label', btn.title);
  btn.style.zIndex = (opts.zIndex || DEFAULTS.zIndex) - 1000;
  btn.addEventListener('click', () => start(steps, opts));
  document.body.appendChild(btn);
  return btn;
}

/** Clear the seen flag so autoStartOnce fires again (dev/testing helper). */
function resetSeen(key, version) {
  try { localStorage.removeItem(seenKey({ key, version })); } catch { /* ignore */ }
}

const WebTour = { start, autoStartOnce, attachHelpButton, resetSeen };
if (typeof window !== 'undefined') window.WebTour = WebTour;

export { start, autoStartOnce, attachHelpButton, resetSeen };
export default WebTour;
