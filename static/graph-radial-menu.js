/* FundsTrace canvas radial menu (node / edge context actions).
 *
 * Portions of the donut geometry, spring integrator, open/hover/group timing and
 * hit-testing rules are adapted from Filee (https://github.com/KnifeLemon/Filee),
 * Copyright (c) 2026 Myeonggu Kim (KnifeLemon) and Filee contributors, MIT.
 * See LICENSES/Filee-MIT.txt and THIRD_PARTY_NOTICES.md ("Filee radial donut menu").
 *
 * Material You palette and DonutMenu surface rendering are adapted from Filee's
 * Services/MaterialYouPalette.cs and Controls/DonutMenu.cs (main 9ca14cd, 2026-11-10):
 * Surface slices at the donut opacity, SubtleBorder, Surface centre, 0.82 accent
 * hover, OnAccent hot labels, corner radius = Theme CornerRadius / 2, soft shadow
 * and outward group chevron. The accent seed is FundsTrace's selected accent.
 *
 * Re-implemented for the FundsTrace G6 canvas: in-page overlay instead of a native
 * always-on-top window; node/edge context menu instead of an Explorer shell entry;
 * one optional group fan instead of flip/faces.
 */
(function (root) {
  'use strict';
  if (root.FundsGraphRadialMenu) return;
  const doc = root.document;
  if (!doc) return;

  const NS = 'http://www.w3.org/2000/svg';
  const TAU = Math.PI * 2;
  const reducedQuery = root.matchMedia ? root.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const reducedMotion = () => !!(reducedQuery && reducedQuery.matches);

  // ── tuning (Filee DonutMenu/DonutGeometry values unless noted) ──────────────
  const DEFAULT_RADIUS = 150;       // Filee DonutMenu default OuterRadius
  const MIN_RADIUS = 150;
  const MAX_RADIUS = 190;
  const INNER_RATIO = 0.42;
  const SLICE_GAP = 3;
  const SLICE_CORNER = 9;           // theme CornerRadius 18 / 2, exactly DonutMenu.SliceCornerRadius
  const HOVER_POP = 7;
  const MARGIN = 14;
  const SUB_EXTRA = 59;             // Filee sub-ring outer 209 at R150
  const SUB_PAD = 13;               // Filee sub-ring inner 163 at R150
  const SUB_RING_SPACE = 62;        // Filee ReserveSubRing window space
  const OPEN_DURATION = 0.30;
  const OPEN_STAGGER = 0.012;
  const OPEN_STAGGER_CAP = 0.072;
  const CLOSE_DURATION = 0.14;
  const GROUP_DWELL = 160;
  const GROUP_COLLAPSE = 140;
  const MIN_SUB_WIDTH = 44;
  const SUB_OVERHANG = 0.25;
  const MIN_CORNER = 0.25;

  // ── geometry (ported from Filee DonutGeometry.cs) ──────────────────────────
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const spanOf = count => count <= 0 ? TAU : TAU / count;
  const midOf = (index, count) => index * spanOf(count);
  const pointAt = (center, radius, angle) => ({
    x: center.x + radius * Math.sin(angle),
    y: center.y - radius * Math.cos(angle),
  });
  const angleOf = (center, point) => {
    const a = Math.atan2(point.x - center.x, center.y - point.y);
    return a < 0 ? a + TAU : a;
  };
  const wrapSigned = angle => angle - TAU * Math.round(angle / TAU);
  const indexAt = (angle, count) => {
    if (count <= 0) return -1;
    const span = spanOf(count);
    return Math.floor((angle + span / 2) / span) % count;
  };
  const fmt = n => Math.round(n * 1000) / 1000;

  function corner(center, outerSide, innerSide, circle, radius, outer) {
    const length = distance(outerSide, innerSide);
    if (length <= 0) return null;
    const u = { x: (innerSide.x - outerSide.x) / length, y: (innerSide.y - outerSide.y) / length };
    const tangent = { x: Math.cos(angleOf(center, outerSide)), y: Math.sin(angleOf(center, outerSide)) };
    let n = { x: -u.y, y: u.x };
    if (n.x * tangent.x + n.y * tangent.y < 0) n = { x: -n.x, y: -n.y };
    const distanceToCircle = outer ? circle - radius : circle + radius;
    const w = { x: outerSide.x + n.x * radius - center.x, y: outerSide.y + n.y * radius - center.y };
    const b = w.x * u.x + w.y * u.y;
    const disc = b * b - (w.x * w.x + w.y * w.y - distanceToCircle * distanceToCircle);
    if (disc < 0) return null;
    const t = -b - Math.sqrt(disc);
    if (t < 0 || t > length) return null;
    const c = { x: outerSide.x + n.x * radius + u.x * t, y: outerSide.y + n.y * radius + u.y * t };
    const toCenter = { x: c.x - center.x, y: c.y - center.y };
    const toCenterLength = Math.hypot(toCenter.x, toCenter.y);
    if (!toCenterLength) return null;
    const onArc = {
      x: center.x + toCenter.x / toCenterLength * circle,
      y: center.y + toCenter.y / toCenterLength * circle,
    };
    return { center: c, onArc, onLine: { x: outerSide.x + u.x * t, y: outerSide.y + u.y * t }, along: t };
  }

  function roundedOutline(center, r1, r2, midAngle, outerHalf, innerHalf, radius) {
    const outerSide = pointAt(center, r2, midAngle - outerHalf);
    const innerSide = pointAt(center, r1, midAngle - innerHalf);
    const o = corner(center, outerSide, innerSide, r2, radius, true);
    const i = corner(center, outerSide, innerSide, r1, radius, false);
    if (!o || !i) return null;
    if (o.along >= i.along || wrapSigned(angleOf(center, o.center) - midAngle) >= 0 ||
      wrapSigned(angleOf(center, i.center) - midAngle) >= 0) return null;
    const mirror = p => pointAt(center, distance(center, p), 2 * midAngle - angleOf(center, p));
    const outerArc = -wrapSigned(angleOf(center, o.center) - midAngle);
    const innerArc = -wrapSigned(angleOf(center, i.center) - midAngle);
    return {
      start: o.onArc,
      steps: [
        { to: mirror(o.onArc), arc: r2, large: outerArc > Math.PI / 2, clockwise: true },
        { to: mirror(o.onLine), arc: radius },
        { to: mirror(i.onLine) },
        { to: mirror(i.onArc), arc: radius },
        { to: i.onArc, arc: r1, large: innerArc > Math.PI / 2, clockwise: false },
        { to: i.onLine, arc: radius },
        { to: o.onLine },
        { to: o.onArc, arc: radius },
      ],
    };
  }

  function outlineToPath(outline) {
    let d = `M ${fmt(outline.start.x)} ${fmt(outline.start.y)}`;
    for (const step of outline.steps) {
      if (step.arc > 0) {
        d += ` A ${fmt(step.arc)} ${fmt(step.arc)} 0 ${step.large ? 1 : 0} ${step.clockwise ? 1 : 0} ` +
          `${fmt(step.to.x)} ${fmt(step.to.y)}`;
      } else {
        d += ` L ${fmt(step.to.x)} ${fmt(step.to.y)}`;
      }
    }
    return d + ' Z';
  }

  function wedgePath(center, r1, r2, midAngle, angleSpan, gap, cornerRadius) {
    const outerHalf = Math.max(0.001, angleSpan / 2 - gap / 2 / r2);
    const innerHalf = Math.max(0.001, angleSpan / 2 - gap / 2 / r1);
    const radius = Math.min(cornerRadius, (r2 - r1) / 2);
    let outline = null;
    if (radius > MIN_CORNER) {
      outline = roundedOutline(center, r1, r2, midAngle, outerHalf, innerHalf, radius);
      if (!outline) {
        let low = 0, high = radius, best = null;
        for (let step = 0; step < 14; step += 1) {
          const candidate = (low + high) / 2;
          const fits = roundedOutline(center, r1, r2, midAngle, outerHalf, innerHalf, candidate);
          if (fits) { best = fits; low = candidate; } else { high = candidate; }
        }
        if (best && low > MIN_CORNER) outline = best;
      }
    }
    if (!outline) {
      outline = {
        start: pointAt(center, r2, midAngle - outerHalf),
        steps: [
          { to: pointAt(center, r2, midAngle + outerHalf), arc: r2, large: outerHalf * 2 > Math.PI, clockwise: true },
          { to: pointAt(center, r1, midAngle + innerHalf) },
          { to: pointAt(center, r1, midAngle - innerHalf), arc: r1, large: innerHalf * 2 > Math.PI, clockwise: false },
        ],
      };
    }
    return outlineToPath(outline);
  }

  function subSpanFor(parentSpan, count, midRadius) {
    if (count <= 0) return 0;
    let span = parentSpan * (1 + 2 * SUB_OVERHANG) / count;
    if (midRadius > 0) span = Math.max(span, MIN_SUB_WIDTH / midRadius);
    return Math.min(span, TAU / count);
  }

  // ── spring / easing (ported from Filee DonutMenu.cs) ───────────────────────
  class Spring {
    constructor(value = 0) { this.value = value; this.velocity = 0; }
    step(target, dt, stiffness = 320, damping = 20) {
      const maxStep = 1 / 120;
      const steps = Math.max(1, Math.ceil(dt / maxStep));
      const h = dt / steps;
      for (let i = 0; i < steps; i += 1) {
        const force = stiffness * (target - this.value) - damping * this.velocity;
        this.velocity += force * h;
        this.value += this.velocity * h;
      }
      if (Math.abs(target - this.value) < 0.001 && Math.abs(this.velocity) < 0.01) {
        this.value = target;
        this.velocity = 0;
        return false;
      }
      return true;
    }
  }
  const easeOutBack = t => 1 + 1.8 * (t - 1) ** 3 + 0.8 * (t - 1) ** 2;
  const easeInCubic = t => t * t * t;


  // ── Material You palette (ported from Filee MaterialYouPalette.cs / SoftPalette.cs) ──
  const DARK_ON_ACCENT = { r: 0x1f, g: 0x1e, b: 0x1d };   // Filee LightPrimary dark label
  const DARK_LABEL = { r: 0x16, g: 0x14, b: 0x1a };        // Filee SoftPalette.OnAccent dark label
  const roundByte = value => Math.max(0, Math.min(255, Math.round(value)));
  function parseColor(value) {
    const text = String(value ?? '').trim();
    let match = /^#([0-9a-f]{3,8})$/i.exec(text);
    if (match) {
      let hex = match[1];
      if (hex.length === 3 || hex.length === 4) hex = hex.split('').map(ch => ch + ch).join('');
      if (hex.length === 6 || hex.length === 8) {
        return {
          r: parseInt(hex.slice(0, 2), 16), g: parseInt(hex.slice(2, 4), 16),
          b: parseInt(hex.slice(4, 6), 16), a: hex.length === 8 ? parseInt(hex.slice(6, 8), 16) : 255,
        };
      }
    }
    match = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+)\s*)?\)$/i.exec(text);
    if (match) return { r: +match[1], g: +match[2], b: +match[3], a: match[4] === undefined ? 255 : Math.round(+match[4] * 255) };
    return null;
  }
  function cssColor(color, alpha) {
    const a = alpha === undefined ? color.a : alpha;
    if (a === undefined || a >= 255) return `rgb(${color.r}, ${color.g}, ${color.b})`;
    return `rgba(${color.r}, ${color.g}, ${color.b}, ${(a / 255).toFixed(4)})`;
  }
  function rgbToHsl(color) {
    const r = color.r / 255, g = color.g / 255, b = color.b / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
    let h = 0, s = 0;
    const l = (max + min) / 2;
    if (delta > 1e-5) {
      s = l < 0.5 ? delta / (max + min) : delta / (2 - max - min);
      if (Math.abs(max - r) < 1e-5) h = ((g - b) / delta + (g < b ? 6 : 0)) / 6;
      else if (Math.abs(max - g) < 1e-5) h = ((b - r) / delta + 2) / 6;
      else h = ((r - g) / delta + 4) / 6;
    }
    return { h, s, l };
  }
  function hslToRgb(h, s, l) {
    const hueToRgb = (p, q, t) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    h = ((h % 1) + 1) % 1;
    s = Math.min(1, Math.max(0, s));
    l = Math.min(1, Math.max(0, l));
    if (s < 1e-5) {
      const grey = roundByte(l * 255);
      return { r: grey, g: grey, b: grey, a: 255 };
    }
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    return {
      r: roundByte(hueToRgb(p, q, h + 1 / 3) * 255),
      g: roundByte(hueToRgb(p, q, h) * 255),
      b: roundByte(hueToRgb(p, q, h - 1 / 3) * 255),
      a: 255,
    };
  }
  function channelLuminance(v) {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  }
  function relativeLuminance(color) {
    return 0.2126 * channelLuminance(color.r) + 0.7152 * channelLuminance(color.g) + 0.0722 * channelLuminance(color.b);
  }
  function contrastRatio(a, b) {
    const la = relativeLuminance(a), lb = relativeLuminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  function onAccent(fill) {
    const white = { r: 255, g: 255, b: 255 };
    return contrastRatio(fill, white) >= contrastRatio(fill, DARK_LABEL) ? white : DARK_LABEL;
  }
  function isMonochrome(color) {
    const { s } = rgbToHsl(color);
    return s < 0.08 || (color.r === color.g && color.g === color.b)
      || (Math.abs(color.r - color.g) <= 4 && Math.abs(color.g - color.b) <= 4 && Math.abs(color.r - color.b) <= 4);
  }
  function lightPrimary(seed, h, sat, l) {
    if (contrastRatio(seed, { r: 255, g: 255, b: 255 }) >= 4.5) return { primary: seed, onPrimary: { r: 255, g: 255, b: 255 } };
    let candidate = seed, lightness = l;
    while (lightness > 0.20 && contrastRatio(candidate, { r: 255, g: 255, b: 255 }) < 4.5) {
      lightness -= 0.005;
      candidate = hslToRgb(h, sat, lightness);
    }
    if (l - lightness <= 0.08) return { primary: candidate, onPrimary: { r: 255, g: 255, b: 255 } };
    if (contrastRatio(seed, DARK_ON_ACCENT) >= 4.5) return { primary: seed, onPrimary: DARK_ON_ACCENT };
    return { primary: candidate, onPrimary: { r: 255, g: 255, b: 255 } };
  }
  function generateMonochrome(isDark) {
    if (!isDark) {
      const accent = { r: 0x1f, g: 0x1e, b: 0x1d, a: 255 };
      return {
        page: { r: 0xf7, g: 0xf6, b: 0xf2, a: 255 }, surface: { r: 255, g: 255, b: 255, a: 255 },
        surfaceAlt: { r: 0xee, g: 0xec, b: 0xe5, a: 255 }, subtleBorder: { r: 0, g: 0, b: 0, a: 24 },
        textPrimary: { r: 0x1f, g: 0x1e, b: 0x1d, a: 255 }, textSecondary: { r: 0x6e, g: 0x6c, b: 0x67, a: 255 },
        danger: { r: 0xc7, g: 0x3a, b: 0x39, a: 255 }, success: { r: 0x0f, g: 0x7a, b: 0x5a, a: 255 },
        warning: { r: 0x9a, g: 0x5b, b: 0x0c, a: 255 }, accent, onAccent: { r: 255, g: 255, b: 255, a: 255 },
      };
    }
    const accent = { r: 0xf1, g: 0xef, b: 0xe8, a: 255 };
    return {
      page: { r: 0x16, g: 0x16, b: 0x14, a: 255 }, surface: { r: 0x22, g: 0x22, b: 0x20, a: 255 },
      surfaceAlt: { r: 0x2c, g: 0x2c, b: 0x29, a: 255 }, subtleBorder: { r: 255, g: 255, b: 255, a: 28 },
      textPrimary: { r: 0xf1, g: 0xef, b: 0xe8, a: 255 }, textSecondary: { r: 0xae, g: 0xac, b: 0xa4, a: 255 },
      danger: { r: 0xf0, g: 0x95, b: 0x95, a: 255 }, success: { r: 0x5d, g: 0xca, b: 0xa5, a: 255 },
      warning: { r: 0xef, g: 0x9f, b: 0x27, a: 255 }, accent, onAccent: { r: 0x1f, g: 0x1e, b: 0x1d, a: 255 },
    };
  }
  function generatePalette(seed, isDark) {
    if (isMonochrome(seed)) return generateMonochrome(isDark);
    const { h, s, l } = rgbToHsl(seed);
    const sat = Math.min(0.85, Math.max(0.20, s));
    if (!isDark) {
      const primary = lightPrimary(seed, h, sat, l);
      return {
        page: hslToRgb(h, Math.min(sat * 0.18, 0.12), 0.97),
        surface: { r: 255, g: 255, b: 255, a: 255 },
        surfaceAlt: hslToRgb(h, Math.min(sat * 0.22, 0.16), 0.92),
        subtleBorder: { ...primary.primary, a: 28 },
        textPrimary: hslToRgb(h, 0.10, 0.12),
        textSecondary: hslToRgb(h, 0.15, 0.38),
        danger: { r: 0xba, g: 0x1a, b: 0x1a, a: 255 },
        success: { r: 0x1b, g: 0x6d, b: 0x24, a: 255 },
        warning: { r: 0x82, g: 0x55, b: 0x00, a: 255 },
        accent: primary.primary, onAccent: primary.onPrimary,
      };
    }
    const darkPrimary = l < 0.65 ? hslToRgb(h, Math.min(sat * 0.75, 0.65), 0.75) : seed;
    return {
      page: hslToRgb(h, Math.min(sat * 0.18, 0.12), 0.08),
      surface: hslToRgb(h, Math.min(sat * 0.20, 0.14), 0.13),
      surfaceAlt: hslToRgb(h, Math.min(sat * 0.22, 0.16), 0.18),
      subtleBorder: { ...darkPrimary, a: 32 },
      textPrimary: hslToRgb(h, 0.08, 0.92),
      textSecondary: hslToRgb(h, 0.12, 0.75),
      danger: { r: 0xff, g: 0xb4, b: 0xab, a: 255 },
      success: { r: 0x8c, g: 0xd6, b: 0x92, a: 255 },
      warning: { r: 0xff, g: 0xba, b: 0x2f, a: 255 },
      accent: darkPrimary, onAccent: onAccent(darkPrimary),
    };
  }
  function materialPalette() {
    let accent = null;
    try {
      accent = parseColor(root.getComputedStyle(doc.documentElement).getPropertyValue('--accent'));
    } catch (_) { accent = null; }
    const seed = accent || { r: 0x7f, g: 0x77, b: 0xdd, a: 255 };
    const isDark = doc.documentElement?.dataset?.ftTheme !== 'light';
    return generatePalette(seed, isDark);
  }
  let menuUid = 0;

  // ── app state helpers (classic scripts expose these as global bindings) ────
  const appState = () => { try { return typeof state === 'undefined' ? null : state; } catch (_) { return null; } };
  const currentContext = () => { try { return typeof caseContext === 'function' ? caseContext() : null; } catch (_) { return null; } };
  const contextCurrent = ctx => { try { return typeof isCurrentCase === 'function' ? isCurrentCase(ctx) : true; } catch (_) { return false; } };
  function ready() {
    const s = appState();
    if (!s || !s.caseId || !s.graph) return false;
    if (s.graphLoading) return false;
    try { if (typeof layoutJob !== 'undefined' && layoutJob) return false; } catch (_) { /* not initialized */ }
    const history = root.FundsCanvasEditor?.history?.status?.();
    return !(history && history.busy);
  }
  function neighborhoodBlocked() {
    try { return typeof neighborhoodBlocker === 'function' ? !!neighborhoodBlocker() : false; } catch (_) { return false; }
  }
  function selectionIds() {
    const s = appState();
    return s?.graphSelection ? [...s.graphSelection.ids] : [];
  }
  function truncate(text, limit) {
    const value = String(text ?? '').replace(/\s+/g, ' ').trim();
    return value.length > limit ? value.slice(0, Math.max(1, limit - 1)) + '…' : value;
  }
  function nodeName(item, model) {
    const data = model?.data || {};
    return String(data.name || model?.name || model?.label || model?.account || model?.id || '账户');
  }
  function nodeAccount(model) {
    const data = model?.data || {};
    const account = String(data.display_account || data.account || model?.account || model?.id || '');
    return account.length > 10 ? '…' + account.slice(-9) : account;
  }
  function edgeTitle(item, model) {
    const data = model?.data || model || {};
    const source = String(model?.source ?? data.source ?? '');
    const target = String(model?.target ?? data.target ?? '');
    const names = data.source_name || data.target_name ? `${data.source_name || source} → ${data.target_name || target}` : '';
    return String(names || model?.label || (source && target ? `${source} → ${target}` : '资金关系'));
  }
  function edgeFlowIds(model) {
    const data = model?.data || model || {};
    return [...new Set([...(model?.ids || []), ...(data.ids || [])].map(String).filter(Boolean))];
  }

  // ── menu models (content is deliberately a small table, easy to tune) ─────
  function nodeMenu(item, multi) {
    const model = item.getModel?.() || {};
    if (multi) {
      return {
        kind: 'node',
        title: `${selectionIds().length} 个账户`,
        subtitle: '多选账户',
        items: [
          { id: 'fit', label: '适应选择', command: 'fitSelection' },
          { id: 'pin', label: '固定布局', command: 'fixLayout' },
          { id: 'unpin', label: '解除固定', command: 'unfixLayout' },
          { id: 'hide', label: '隐藏选中', command: 'hideSelection' },
          { id: 'only', label: '只看选中', command: 'showOnlySelection' },
          { id: 'clear', label: '取消选择', command: 'clearSelection' },
        ],
      };
    }
    const id = String(model.id ?? '');
    const s = appState();
    const canContinue = !!(s?.graphView?.visibleIds && String(s.graphView.root) === id && Number(s.graphView.remaining) > 0);
    return {
      kind: 'node',
      title: truncate(nodeName(item, model), 10),
      subtitle: nodeAccount(model) ? `账户 · ${nodeAccount(model)}` : '账户',
      items: [
        { id: 'inspect', label: '详情', command: 'inspectSelection' },
        { id: 'flows', label: '查流水', command: 'viewFlows' },
        {
          id: 'expand', label: '展开邻域', ariaLabel: '展开邻域', group: {
            command: 'expandNeighborhood',
            children: [
              { id: 'both', label: '双向', ariaLabel: '双向展开邻域', value: 'both' },
              { id: 'in', label: '转入', ariaLabel: '只看转入', value: 'in' },
              { id: 'out', label: '转出', ariaLabel: '只看转出', value: 'out' },
              ...(canContinue ? [{ id: 'more', label: '继续', ariaLabel: '继续展开当前中心', value: 'more' }] : []),
            ],
          },
        },
        { id: 'fit', label: '适应选择', command: 'fitSelection' },
        { id: 'emphasize', label: '重点展示', command: 'emphasize' },
        { id: 'annotate', label: '添加标注', command: 'annotateSelection' },
        { id: 'hide', label: '隐藏选中', command: 'hideSelection' },
        { id: 'only', label: '只看选中', command: 'showOnlySelection' },
        { id: 'remove', label: '移出图谱', command: 'removeNodeFromCanvas', danger: true },
      ],
    };
  }

  function edgeMenu(item) {
    const model = item.getModel?.() || {};
    const selfLoop = String(model.source ?? '') === String(model.target ?? '');
    let bold = false;
    try {
      const key = typeof edgeKey === 'function' ? edgeKey(model) : `${model.source}=>${model.target}`;
      const style = appState()?.presentation?.edgeLabels?.[key] || {};
      bold = style.fontWeight === 'bold' || style.fontWeight === '700';
    } catch (_) { /* optional presentation state */ }
    return {
      kind: 'edge',
      title: truncate(edgeTitle(item, model), 12),
      subtitle: '资金关系',
      items: [
        { id: 'inspect', label: '详情', command: 'inspectSelection' },
        { id: 'source', label: '付款方', command: 'inspectEdgeEndpoint', value: 'source' },
        { id: 'target', label: '收款方', command: 'inspectEdgeEndpoint', value: 'target' },
        { id: 'fit', label: '适应选择', command: 'fitSelection' },
        {
          id: 'type', label: '线型', ariaLabel: '关系线型', group: {
            command: 'edgeType',
            children: [
              { id: 'quadratic', label: '曲线', ariaLabel: '曲线', value: 'quadratic' },
              { id: 'line', label: '直线', ariaLabel: '直线', value: 'line' },
              { id: 'polyline', label: '折线', ariaLabel: '折线', value: 'polyline' },
            ],
          },
          disabled: selfLoop,
        },
        { id: 'bold', label: bold ? '取消加粗' : '标签加粗', command: 'labelBold', value: !bold },
        { id: 'remove', label: '移出该关系', command: 'removeEdgeFromCanvas', danger: true },
      ],
    };
  }

  function canvasShell() {
    try { return doc.getElementById('workspace')?.fundsWorkspaceShell || null; } catch (_) { return null; }
  }
  function blankMenu() {
    const shell = canvasShell();
    const shellState = shell?.getState?.() || { view: 'standard', sidebarCollapsed: false };
    const legend = doc.getElementById('legend');
    const minimapToggle = doc.getElementById('btnMinimapToggle');
    const minimapOn = minimapToggle?.getAttribute('aria-expanded') === 'true';
    const view = ['standard', 'split', 'focus'].includes(shellState.view) ? shellState.view : 'standard';
    const viewLabel = view === 'split' ? '切换标准' : '切换上下';
    const viewSubtitle = view === 'split' ? '视图 · 上下' : view === 'focus' ? '视图 · 专注' : '视图 · 标准';
    const undo = doc.getElementById('btnUndo');
    const save = doc.getElementById('btnCanvasSave');
    const exportButton = doc.getElementById('btnExportActions');
    const relayout = doc.getElementById('btnRelayout');
    const fit = doc.getElementById('btnFit');
    const nodes = appState()?.graph?.getNodes?.().length || 0;
    const unavailable = element => !element || !!element.disabled;
    return {
      kind: 'blank',
      title: '画布',
      subtitle: viewSubtitle,
      items: [
        { id: 'selectAll', label: '全选', command: 'canvasAction', value: 'selectAll', disabled: nodes <= 0 },
        { id: 'view', label: viewLabel, command: 'canvasAction', value: 'view' },
        { id: 'legend', label: legend?.hidden ? '显示图例' : '隐藏图例', command: 'canvasAction', value: 'legend', disabled: !legend },
        { id: 'minimap', label: minimapOn ? '隐藏缩略' : '显示缩略', command: 'canvasAction', value: 'minimap', disabled: !minimapToggle },
        { id: 'sidebar', label: shellState.sidebarCollapsed ? '显示查询' : '隐藏查询', command: 'canvasAction', value: 'sidebar', disabled: !shell },
        { id: 'fit', label: '适应画布', command: 'canvasAction', value: 'fit', disabled: unavailable(fit) },
        { id: 'relayout', label: '重新布局', command: 'canvasAction', value: 'relayout', disabled: unavailable(relayout) },
        { id: 'undo', label: '撤回', command: 'canvasAction', value: 'undo', disabled: unavailable(undo) },
        { id: 'save', label: '保存画布', command: 'canvasAction', value: 'save', disabled: unavailable(save) },
        { id: 'export', label: '导出', command: 'canvasAction', value: 'export', disabled: unavailable(exportButton) },
      ],
    };
  }

  function itemEnabled(spec, context) {
    const { baseReady, blocked, kind, model, multi } = context;
    if (!baseReady && kind !== 'blank') return false;
    if (spec.disabled) return false;
    const ids = selectionIds();
    const cameraOk = () => {
      try { return typeof graphCameraInteractionAllowed === 'function' ? !!graphCameraInteractionAllowed() : true; } catch (_) { return true; }
    };
    if (spec.group) {
      if (kind === 'node' && blocked) return false;
      return spec.group.children.length > 0;
    }
    if (spec.command === 'inspectSelection') {
      const dragging = !!appState()?.graphSelection?.status?.().dragging;
      return !dragging && (kind === 'edge' ? ids.length === 0 : ids.length === 1);
    }
    if (spec.command === 'viewFlows') return ids.length === 1;
    if (spec.command === 'emphasize') return ids.length === 1;
    if (spec.command === 'annotateSelection') {
      try { return ids.length === 1 && (typeof annotationReady !== 'function' || annotationReady()); } catch (_) { return false; }
    }
    if (spec.command === 'fitSelection') return cameraOk();
    if (spec.command === 'removeEdgeFromCanvas') return ids.length === 0 && edgeFlowIds(model).length > 0;
    if (spec.command === 'removeNodeFromCanvas') return ids.length === 1;
    if (spec.command === 'inspectEdgeEndpoint') return ids.length === 0;
    if (spec.command === 'canvasAction') return !spec.disabled;
    if (spec.command === 'fixLayout' || spec.command === 'unfixLayout' || spec.command === 'clearSelection' || spec.command === 'labelBold') return true;
    return true;
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function svgEl(name, attrs) {
    const el = doc.createElementNS(NS, name);
    if (attrs) for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    return el;
  }
  function buttonEl(className, text) {
    const el = doc.createElement('button');
    el.type = 'button';
    el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function placementBounds(container, side) {
    const viewport = {
      left: 8, top: 8,
      right: Math.max(8, root.innerWidth - 8),
      bottom: Math.max(8, root.innerHeight - 8),
    };
    let rect = null;
    try { rect = container?.getBoundingClientRect?.() || null; } catch (_) { rect = null; }
    if (!rect || rect.width <= 0 || rect.height <= 0) return viewport;
    const graph = { left: rect.left + 8, top: rect.top + 8, right: rect.right - 8, bottom: rect.bottom - 8 };
    const widthFits = graph.right - graph.left >= side;
    const heightFits = graph.bottom - graph.top >= side;
    if (!widthFits && !heightFits) return viewport;
    return {
      left: graph.left,
      right: widthFits ? graph.right : Math.max(graph.right, graph.left + side),
      top: graph.top,
      bottom: heightFits ? graph.bottom : Math.max(graph.bottom, graph.top + side),
    };
  }
  function placeMenu(rootEl, anchor, side, container) {
    const bounds = placementBounds(container, side);
    const maxLeft = Math.max(bounds.left, bounds.right - side);
    const maxTop = Math.max(bounds.top, bounds.bottom - side);
    rootEl.style.left = clamp(anchor.x - side / 2, bounds.left, maxLeft) + 'px';
    rootEl.style.top = clamp(anchor.y - side / 2, bounds.top, maxTop) + 'px';
  }
  function focusGraph() {
    try { doc.getElementById('graphContainer')?.focus?.({ preventScroll: true }); } catch (_) { /* gone */ }
  }

  // ── live menu state ────────────────────────────────────────────────────────
  let menu = null;
  let serial = 0;
  let dismissal = null;

  function sideFor(radius, reserveSubRing = true) {
    return Math.ceil(2 * (radius + (reserveSubRing ? SUB_RING_SPACE : 0) + HOVER_POP + MARGIN));
  }
  function menuRadiusFor() {
    return DEFAULT_RADIUS;
  }
  function groupMarkPath(center, radius, mid, scale = 1) {
    const inset = 7 * scale, depth = 4.5 * scale, halfWidth = 5 * scale;
    const tip = pointAt(center, radius - inset, mid);
    const back = radius - inset - depth;
    const turn = halfWidth / Math.max(1, back);
    const start = pointAt(center, back, mid - turn);
    const end = pointAt(center, back, mid + turn);
    return `M ${fmt(start.x)} ${fmt(start.y)} L ${fmt(tip.x)} ${fmt(tip.y)} L ${fmt(end.x)} ${fmt(end.y)}`;
  }

  function hitTest(point) {
    if (!menu) return { kind: 'outside' };
    const center = menu.center, inner = menu.inner, outer = menu.radius;
    const d = Math.hypot(point.x - center.x, point.y - center.y);
    if (d < inner) return { kind: 'hub' };
    const group = menu.group;
    if (group && group.open && group.expand.value > 0.02 && d >= group.subInner && d <= group.subOuter) {
      const delta = wrapSigned(angleOf(center, point) - group.parentMid);
      const sub = Math.floor(delta / group.subSpan + group.count / 2);
      if (sub >= 0 && sub < group.count) return { kind: 'sub', index: group.index, sub };
    }
    if (d > outer + HOVER_POP) return { kind: 'outside' };
    return { kind: 'segment', index: indexAt(angleOf(center, point), menu.count) };
  }

  function menuPoint(event) {
    const rect = menu.rootEl.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function refreshHoverTargets() {
    if (!menu) return;
    const group = menu.group;
    menu.entries.forEach((entry, index) => {
      entry.target = index === menu.activeIndex || (menu.activeSub >= 0 && group && index === group.index) ? 1 : 0;
    });
    if (group) group.subEntries.forEach((entry, index) => { entry.target = index === menu.activeSub ? 1 : 0; });
    ensureLoop();
  }

  function refreshActiveClasses() {
    if (!menu) return;
    const groupIndex = menu.group ? menu.group.index : -1;
    menu.entries.forEach((entry, index) => {
      const active = index === menu.activeIndex || (menu.activeSub >= 0 && index === groupIndex);
      entry.labelEl.classList.toggle('is-active', active);
      entry.sliceEl.classList.toggle('is-active', active);
    });
    if (menu.group) menu.group.subEntries.forEach((entry, index) => {
      entry.labelEl.classList.toggle('is-active', index === menu.activeSub);
      entry.sliceEl.classList.toggle('is-active', index === menu.activeSub);
    });
  }

  function setActive(index, sub = -1) {
    if (!menu || menu.closing) return;
    const changed = index !== menu.activeIndex || sub !== menu.activeSub;
    if (!changed) return;
    menu.activeIndex = index;
    menu.activeSub = sub;
    refreshHoverTargets();
    refreshActiveClasses();
    const group = menu.group;
    clearTimeout(menu.expandTimer);
    clearTimeout(menu.collapseTimer);
    if (!group) return;
    if (sub >= 0) return;
    if (index === group.index) {
      if (!group.open) {
        menu.expandTimer = root.setTimeout(() => {
          if (!menu || menu.activeIndex !== group.index || menu.activeSub >= 0) return;
          expandGroup();
        }, GROUP_DWELL);
      } else {
        group.target = 1;
        ensureLoop();
      }
    } else if (group.open) {
      menu.collapseTimer = root.setTimeout(() => {
        if (!menu || menu.activeSub >= 0 || menu.activeIndex === group.index) return;
        collapseGroup();
      }, GROUP_COLLAPSE);
    }
  }

  function expandGroup() {
    if (!menu || !menu.group) return;
    const group = menu.group;
    if (group.open) return;
    group.open = true;
    group.target = 1;
    group.labelEls.forEach(el => { el.hidden = false; el.style.pointerEvents = 'auto'; });
    menu.rootEl.classList.add('is-group-open');
    ensureLoop();
  }

  function collapseGroup() {
    if (!menu || !menu.group) return;
    const group = menu.group;
    group.open = false;
    group.target = 0;
    group.labelEls.forEach(el => { el.style.pointerEvents = 'none'; });
    menu.rootEl.classList.remove('is-group-open');
    ensureLoop();
  }

  function toggleGroup() {
    if (!menu || !menu.group) return;
    if (menu.group.open) collapseGroup(); else expandGroup();
  }

  // ── commands ───────────────────────────────────────────────────────────────
  function tokenCurrent(token) {
    const s = appState();
    if (!s || !token) return false;
    if (s.graph !== token.graph || s.graphView !== token.view) return false;
    if (!contextCurrent(token.context)) return false;
    if (token.revision !== s.graphView?.revision) return false;
    return true;
  }
  function requestCommand(command, value) {
    if (!menu || !command) return;
    const token = menu.binding;
    const detail = value === undefined ? { command } : { command, value };
    closeMenu(false, () => {
      if (!tokenCurrent(token)) return;
      try { doc.dispatchEvent(new root.CustomEvent('funds:ribbon-command', { detail })); } catch (_) { /* host missing */ }
    });
  }
  function activateGroupChild(entry) {
    if (!entry || !entry.enabled) return;
    const group = menu?.group;
    if (!group) return;
    requestCommand(group.spec.group.command, entry.spec.value);
  }
  function activateItem(entry) {
    if (!entry || !entry.enabled) return;
    if (entry.spec.group) { toggleGroup(); return; }
    requestCommand(entry.spec.command, entry.spec.value);
  }

  // ── rendering frame ────────────────────────────────────────────────────────
  function applyFrame() {
    if (!menu) return;
    const center = menu.center;
    for (const entry of menu.entries) {
      const open = clamp(menu.openVals[entry.index] ?? 0, 0, 1.2);
      const hover = entry.hover.value;
      const angle = entry.mid;
      const offset = { x: Math.sin(angle) * HOVER_POP * hover, y: -Math.cos(angle) * HOVER_POP * hover };
      const scale = (0.78 + 0.22 * open) * (1 + hover * 0.025);
      const rotate = (1 - Math.min(open, 1)) * (-0.12) * (180 / Math.PI);
      const ox = center.x + offset.x, oy = center.y + offset.y;
      entry.sliceEl.setAttribute('transform',
        `translate(${fmt(ox)} ${fmt(oy)}) rotate(${fmt(rotate)}) scale(${fmt(scale)}) ` +
        `translate(${fmt(-center.x)} ${fmt(-center.y)})`);
      entry.accentEl.setAttribute('opacity', fmt(clamp(0.82 * hover, 0, 1)));
      entry.labelEl.style.opacity = fmt(clamp(open, 0, 1));
      entry.labelEl.style.transform = `translate(${fmt((1 - open) * (center.x - entry.base.x) + offset.x)}px, ` +
        `${fmt((1 - open) * (center.y - entry.base.y) + offset.y)}px) scale(${fmt(scale)})`;
      entry.sliceEl.style.opacity = fmt(clamp(open, 0, 1.2));
    }

    if (menu.hubEl) {
      const openAverage = menu.openVals.reduce((sum, value) => sum + clamp(value, 0, 1), 0) / Math.max(1, menu.openVals.length);
      const hubScale = 0.86 + 0.14 * clamp(openAverage, 0, 1);
      menu.hubEl.style.opacity = fmt(clamp(openAverage * 1.15, 0, 1));
      menu.hubEl.style.transform = `scale(${fmt(hubScale)})`;
      if (menu.shadowEl) menu.shadowEl.style.opacity = fmt(clamp(openAverage, 0, 1));
    }

    const group = menu.group;
    if (group) {
      const expand = clamp(group.expand.value, 0, 1.1);
      group.visual.setAttribute('transform',
        `translate(${fmt(group.anchor.x)} ${fmt(group.anchor.y)}) scale(${fmt(0.8 + 0.2 * expand)}) ` +
        `translate(${fmt(-group.anchor.x)} ${fmt(-group.anchor.y)})`);
      group.visual.style.opacity = fmt(clamp(expand, 0, 1));
      const visible = group.open || expand > 0.005;
      group.labelEls.forEach((el, index) => {
        el.hidden = !visible;
        const entry = group.subEntries[index];
        const base = entry.base;
        const scaledX = group.anchor.x + (base.x - group.anchor.x) * (0.8 + 0.2 * expand);
        const scaledY = group.anchor.y + (base.y - group.anchor.y) * (0.8 + 0.2 * expand);
        const hover = entry.hover.value;
        const angle = entry.mid;
        const offset = { x: Math.sin(angle) * HOVER_POP * hover, y: -Math.cos(angle) * HOVER_POP * hover };
        const scale = (0.8 + 0.2 * expand) * (1 + hover * 0.025);
        el.style.opacity = fmt(clamp(expand, 0, 1));
        el.style.transform = `translate(${fmt(scaledX - base.x + offset.x)}px, ${fmt(scaledY - base.y + offset.y)}px) ` +
          `scale(${fmt(scale)})`;
        entry.sliceEl.setAttribute('opacity', fmt(clamp(expand, 0, 1)));
        entry.accentEl.setAttribute('opacity', fmt(clamp(0.82 * hover, 0, 1)));
      });
    }
  }

  function ensureLoop() {
    if (!menu) return;
    if (menu.reduced) {
      menu.openClock = null;
      menu.openVals.fill(1);
      menu.entries.forEach(entry => { entry.hover.value = entry.target; });
      if (menu.group) {
        menu.group.expand.value = menu.group.target;
        menu.group.labelEls.forEach(el => { el.hidden = menu.group.target <= 0; });
      }
      applyFrame();
      if (menu.closing) finishClose();
      return;
    }
    if (menu.raf) return;
    menu.lastTs = 0;
    menu.raf = root.requestAnimationFrame(frame);
  }

  function frame(timestamp) {
    if (!menu) return;
    menu.raf = 0;
    const now = timestamp || root.performance?.now?.() || Date.now();
    if (!menu.lastTs) menu.lastTs = now;
    const elapsed = clamp((now - menu.lastTs) / 1000, 0, 0.1);
    menu.lastTs = now;
    const dt = Math.min(elapsed, 0.05);
    let active = false;

    if (menu.openClock !== null) {
      menu.openClock += elapsed;
      let done = true;
      for (let i = 0; i < menu.entries.length; i += 1) {
        const delay = menu.closing ? 0 : Math.min(i * OPEN_STAGGER, OPEN_STAGGER_CAP);
        const duration = menu.closing ? CLOSE_DURATION : OPEN_DURATION;
        const t = clamp((menu.openClock - delay) / duration, 0, 1);
        menu.openVals[i] = menu.closing
          ? (menu.closeFrom[i] ?? 1) * (1 - easeInCubic(t))
          : easeOutBack(t);
        if (t < 1) done = false;
      }
      if (done) {
        menu.openClock = null;
        if (menu.closing) { finishClose(); return; }
      } else {
        active = true;
      }
    }

    for (const entry of menu.entries) active = entry.hover.step(entry.target, dt, 320, entry.target > 0.5 ? 20 : 36) || active;
    const group = menu.group;
    if (group) {
      for (const entry of group.subEntries) active = entry.hover.step(entry.target, dt, 320, entry.target > 0.5 ? 20 : 36) || active;
      active = group.expand.step(group.target, dt, 320, group.target > 0.5 ? 24 : 36) || active;
      if (group.target <= 0 && group.expand.value <= 0.001) {
        group.labelEls.forEach(el => { el.hidden = true; });
      }
    }
    applyFrame();
    if (active || menu.openClock !== null) {
      menu.raf = root.requestAnimationFrame(frame);
    } else {
      menu.lastTs = 0;
    }
  }

  // ── construction ───────────────────────────────────────────────────────────
  function createMenu(options) {
    const { spec, radius, anchor, container } = options;
    const side = sideFor(radius, spec.items.some(item => item.group));
    const center = { x: side / 2, y: side / 2 };
    const count = spec.items.length;
    const inner = radius * INNER_RATIO;
    const rootEl = doc.createElement('div');
    rootEl.className = 'funds-radial';
    rootEl.setAttribute('role', 'menu');
    rootEl.setAttribute('aria-label', spec.kind === 'edge' ? '关系操作' : spec.kind === 'blank' ? '画布操作' : '账户操作');
    rootEl.style.width = side + 'px';
    rootEl.style.height = side + 'px';
    placeMenu(rootEl, anchor, side, container);

    const palette = materialPalette();
    rootEl.style.setProperty('--radial-surface', cssColor(palette.surface));
    rootEl.style.setProperty('--radial-surface-alt', cssColor(palette.surfaceAlt));
    rootEl.style.setProperty('--radial-border', cssColor(palette.subtleBorder));
    rootEl.style.setProperty('--radial-text', cssColor(palette.textPrimary));
    rootEl.style.setProperty('--radial-text-secondary', cssColor(palette.textSecondary));
    rootEl.style.setProperty('--radial-accent', cssColor(palette.accent));
    rootEl.style.setProperty('--radial-on-accent', cssColor(palette.onAccent));
    rootEl.style.setProperty('--radial-danger', cssColor(palette.danger));

    const svg = svgEl('svg', { class: 'funds-radial-art', width: side, height: side, viewBox: `0 0 ${side} ${side}`, 'aria-hidden': 'true' });
    const shadowId = `funds-radial-shadow-${++menuUid}`;
    const shadowRadius = radius + 14;
    const defs = svgEl('defs');
    const gradient = svgEl('radialGradient', {
      id: shadowId, gradientUnits: 'userSpaceOnUse',
      cx: center.x, cy: center.y + 4, r: shadowRadius,
    });
    for (const [offset, opacity] of [
      [0, 0],
      [(radius - 18) / shadowRadius, 0],
      [(radius - 2) / shadowRadius, 46 / 255],
      [1, 0],
    ]) gradient.appendChild(svgEl('stop', { offset, 'stop-color': '#000', 'stop-opacity': opacity }));
    defs.appendChild(gradient);
    const shadowEl = svgEl('circle', {
      class: 'funds-radial-shadow', cx: center.x, cy: center.y + 4,
      r: shadowRadius, fill: `url(#${shadowId})`, opacity: 0,
    });
    const sliceLayer = svgEl('g', { class: 'funds-radial-slices' });
    const subLayer = svgEl('g', { class: 'funds-radial-subring' });
    subLayer.style.opacity = '0';
    svg.append(defs, shadowEl, sliceLayer, subLayer);
    rootEl.appendChild(svg);

    const baseReady = ready();
    const blocked = neighborhoodBlocked();
    const entries = spec.items.map((item, index) => {
      const mid = midOf(index, count);
      const base = pointAt(center, (inner + radius) / 2 - (item.group ? 3 : 0), mid);
      const path = wedgePath(center, inner, radius, mid, spanOf(count), SLICE_GAP, SLICE_CORNER);
      const sliceEl = svgEl('g', { class: 'funds-radial-slice', 'data-index': index });
      if (item.danger) sliceEl.classList.add('is-danger');
      if (item.group) sliceEl.classList.add('is-group-slice');
      const wedge = svgEl('path', { class: 'funds-radial-wedge', d: path });
      const accent = svgEl('path', { class: 'funds-radial-accent', d: path, opacity: 0 });
      sliceEl.append(wedge, accent);
      if (item.group) {
        const scale = radius / DEFAULT_RADIUS;
        sliceEl.appendChild(svgEl('path', {
          class: 'funds-radial-group-mark',
          d: groupMarkPath(center, radius, mid, scale),
          'stroke-width': fmt(1.6 * scale),
        }));
      }
      sliceLayer.appendChild(sliceEl);
      const enabled = itemEnabled(item, { baseReady, blocked, kind: spec.kind, model: options.model });
      if (!enabled) sliceEl.classList.add('is-disabled');
      return {
        spec: item, index, mid, base, sliceEl, accentEl: accent,
        enabled, hover: new Spring(0), target: 0,
      };
    });

    const groupEntry = entries.find(entry => entry.spec.group);
    let group = null;
    if (groupEntry) {
      const children = groupEntry.spec.group.children;
      const subInner = radius + SUB_PAD;
      const subOuter = radius + SUB_EXTRA;
      const parentMid = groupEntry.mid;
      const subMidRadius = (subInner + subOuter) / 2;
      const subSpan = subSpanFor(spanOf(count), children.length, subMidRadius);
      const anchorPoint = pointAt(center, subInner, parentMid);
      const subEntries = children.map((child, index) => {
        const mid = parentMid + (index - (children.length - 1) / 2) * subSpan;
        const base = pointAt(center, subMidRadius, mid);
        const path = wedgePath(center, subInner, subOuter, mid, subSpan, SLICE_GAP, SLICE_CORNER);
        const sliceEl = svgEl('g', { class: 'funds-radial-slice funds-radial-subslice', 'data-index': index });
        if (child.danger) sliceEl.classList.add('is-danger');
        const wedge = svgEl('path', { class: 'funds-radial-wedge', d: path });
        const accent = svgEl('path', { class: 'funds-radial-accent', d: path, opacity: 0 });
        sliceEl.append(wedge, accent);
        subLayer.appendChild(sliceEl);
        const enabled = baseReady && (spec.kind === 'edge' || !blocked);
        if (!enabled) sliceEl.classList.add('is-disabled');
        return {
          spec: child, index, mid, base, sliceEl, accentEl: accent,
          enabled, hover: new Spring(0), target: 0,
        };
      });
      group = {
        index: groupEntry.index, spec: groupEntry.spec, parentMid, count: children.length,
        subInner, subOuter, subSpan, anchor: anchorPoint, visual: subLayer,
        expand: new Spring(0), target: 0, open: false,
        subEntries, labelEls: [],
      };
    }

    const maxLabel = entries.reduce((max, entry) => Math.max(max, (entry.spec.label || '').length), 0);
    const labelWidth = clamp(maxLabel * 13 + 8, 54, 108);
    const labelHeight = 32;
    const labelLayer = doc.createElement('div');
    labelLayer.className = 'funds-radial-labels';
    rootEl.appendChild(labelLayer);

    for (const entry of entries) {
      const label = buttonEl('funds-radial-item', entry.spec.label);
      label.setAttribute('role', 'menuitem');
      if (entry.spec.group) {
        label.setAttribute('aria-haspopup', 'menu');
        label.setAttribute('aria-expanded', 'false');
        label.classList.add('is-group');
        label.setAttribute('aria-label', entry.spec.ariaLabel || entry.spec.label);
      }
      if (entry.spec.danger) label.classList.add('is-danger');
      if (!entry.enabled) { label.disabled = true; label.setAttribute('aria-disabled', 'true'); }
      label.style.left = (entry.base.x - labelWidth / 2) + 'px';
      label.style.top = (entry.base.y - labelHeight / 2) + 'px';
      label.style.width = labelWidth + 'px';
      label.style.height = labelHeight + 'px';
      label.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        activateItem(entry);
      });
      labelLayer.appendChild(label);
      entry.labelEl = label;
    }

    if (group) {
      const subWidth = 42;
      const subHeight = 27;
      group.subEntries.forEach(entry => {
        const label = buttonEl('funds-radial-item funds-radial-subitem', entry.spec.label);
        label.setAttribute('role', 'menuitem');
        label.setAttribute('aria-label', entry.spec.ariaLabel || entry.spec.label);
        if (!entry.enabled) { label.disabled = true; label.setAttribute('aria-disabled', 'true'); }
        label.hidden = true;
        label.style.left = (entry.base.x - subWidth / 2) + 'px';
        label.style.top = (entry.base.y - subHeight / 2) + 'px';
        label.style.width = subWidth + 'px';
        label.style.height = subHeight + 'px';
        label.style.pointerEvents = 'none';
        label.addEventListener('click', event => {
          event.preventDefault();
          event.stopPropagation();
          activateGroupChild(entry);
        });
        labelLayer.appendChild(label);
        entry.labelEl = label;
        group.labelEls.push(label);
      });
    }

    const hubSize = Math.max(64, 2 * inner - 12);
    const hubEl = buttonEl('funds-radial-hub');
    hubEl.setAttribute('aria-label', `关闭菜单 · ${spec.title}`);
    hubEl.style.width = hubSize + 'px';
    hubEl.style.height = hubSize + 'px';
    hubEl.style.left = (center.x - hubSize / 2) + 'px';
    hubEl.style.top = (center.y - hubSize / 2) + 'px';
    const hubTitle = doc.createElement('span');
    hubTitle.className = 'funds-radial-hub-title';
    hubTitle.textContent = spec.title;
    const hubSub = doc.createElement('span');
    hubSub.className = 'funds-radial-hub-sub';
    hubSub.textContent = spec.subtitle;
    hubEl.append(hubTitle, hubSub);
    hubEl.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); closeMenu(true); });
    labelLayer.appendChild(hubEl);

    return {
      rootEl, svg, center, radius, inner, count, spec, shadowEl,
      entries, group, hubEl,
      openVals: entries.map(() => 0), closeFrom: entries.map(() => 0),
      openClock: 0, lastTs: 0, raf: 0, reduced: reducedMotion(),
      closing: false, onClosed: null, activeIndex: -1, activeSub: -1,
      expandTimer: 0, collapseTimer: 0,
      pointerId: null,
    };
  }

  function bindGlobal(menuState, options) {
    const listeners = [];
    const on = (target, type, handler, opts) => {
      target.addEventListener(type, handler, opts);
      listeners.push([target, type, handler, opts]);
    };
    menuState.unbind = () => {
      for (const [target, type, handler, opts] of listeners) target.removeEventListener(type, handler, opts);
      listeners.length = 0;
      root.clearTimeout(menuState.expandTimer);
      root.clearTimeout(menuState.collapseTimer);
      if (menuState.raf) root.cancelAnimationFrame(menuState.raf);
      menuState.raf = 0;
    };

    const outside = event => {
      if (!menu || event.target && menu.rootEl.contains(event.target)) return;
      if (event.type === 'pointerdown' && event.button === 2) {
        // Right-click elsewhere behaves like a native menu: close, then let the
        // next contextmenu open its own menu (G6 node/edge or the system menu).
        dismissal = null;
        closeMenu(false);
        return;
      }
      const now = Date.now();
      dismissal = { x: event.clientX || 0, y: event.clientY || 0, until: now + 2000 };
      closeMenu(true, () => focusGraph());
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    const suppressed = event => {
      if (!dismissal) return false;
      const now = Date.now();
      const types = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu'];
      if (now > dismissal.until) { dismissal = null; return false; }
      if (!types.includes(event.type)) return false;
      const samePlace = Math.hypot((event.clientX || 0) - dismissal.x, (event.clientY || 0) - dismissal.y) < 8;
      const inGraph = !!event.target?.closest?.('#graphContainer,#graphMinimapPanel,#graphMinimap');
      if (!samePlace && !inGraph) { dismissal = null; return false; }
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.type === 'click' || event.type === 'contextmenu' || event.type === 'pointercancel') dismissal = null;
      return true;
    };

    on(doc, 'pointerdown', outside, true);
    for (const type of ['pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu']) {
      on(doc, type, event => { if (suppressed(event)) return; }, true);
    }
    const onKey = event => {
      if (!menu) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true, () => focusGraph());
      }
    };
    on(doc, 'keydown', onKey, true);
    const closeFor = () => { if (menu) closeMenu(true); };
    on(doc, 'visibilitychange', () => { if (doc.hidden) closeFor(); });
    on(root, 'blur', closeFor);
    on(root, 'resize', closeFor);
    on(doc, 'funds:graph-metric', event => { if (event.detail?.phase === 'reset') closeFor(); });
    on(doc, 'funds:feedback-binding', closeFor);
    on(doc, 'funds:selection-drag-start', closeFor);
    on(doc, 'fundstrace:workspace-resize', closeFor);
    on(doc, 'funds:toolbar-menu-change', closeFor);
    on(doc, 'funds-appearance-change', closeFor);
    return menuState;
  }

  function openMenu(options) {
    closeMenu(false);
    serial += 1;
    const created = createMenu(options);
    menu = created;
    menu.binding = options.binding;
    menu.model = options.model;
    menu.kind = options.kind;
    menu.reduced = reducedMotion();
    bindGlobal(menu);
    applyFrame();

    // Group sub-ring starts hidden; group interaction is only possible while open.
    if (menu.group) menu.group.labelEls.forEach(el => { el.hidden = true; });

    doc.body.appendChild(menu.rootEl);
    const focusables = menu.entries.filter(entry => entry.enabled).map(entry => entry.labelEl);
    root.setTimeout(() => {
      if (menu !== created) return;
      focusables[0]?.focus?.({ preventScroll: true });
    }, 0);
    if (!menu.reduced) ensureLoop(); else ensureLoop();
    return created;
  }

  function closeMenu(animate, onClosed) {
    if (!menu) { onClosed?.(); return; }
    const current = menu;
    if (current.closing) {
      if (onClosed) {
        const previous = current.onClosed;
        current.onClosed = () => { try { previous?.(); } finally { onClosed(); } };
      }
      return;
    }
    if (!animate || current.reduced) {
      menu = null;
      current.unbind?.();
      current.rootEl.remove();
      onClosed?.();
      return;
    }
    current.closing = true;
    current.closeFrom = current.openVals.slice();
    current.openClock = 0;
    current.onClosed = onClosed;
    current.rootEl.classList.add('is-closing');
    current.rootEl.style.pointerEvents = 'none';
    ensureLoop();
  }

  function finishClose() {
    if (!menu) return;
    const current = menu;
    const done = current.onClosed;
    menu = null;
    current.unbind?.();
    current.rootEl.remove();
    done?.();
  }

  // ── interaction handlers ───────────────────────────────────────────────────
  function installMenuHandlers(menuState) {
    const rootEl = menuState.rootEl;
    const keydown = event => {
      if (!menu || menu !== menuState) return;
      if (event.key === 'Tab') {
        event.preventDefault();
        closeMenu(true, () => focusGraph());
        return;
      }
      if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
          const target = event.target?.closest?.('.funds-radial-item');
          if (target && !target.disabled) { event.preventDefault(); target.click(); }
        }
        return;
      }
      event.preventDefault();
      const group = menu.group;
      const current = doc.activeElement;
      const subButtons = group ? group.labelEls.filter(el => !el.hidden && !el.disabled) : [];
      const subIndex = subButtons.indexOf(current);
      if (subIndex >= 0) {
        const next = ['ArrowRight', 'ArrowDown', 'Home'].includes(event.key) ? subIndex + 1
          : event.key === 'End' ? subButtons.length - 1 : subIndex - 1;
        if (next >= 0 && next < subButtons.length) subButtons[next].focus({ preventScroll: true });
        else if (next < 0) group.labelEls.find(el => el === menu.entries[group.index].labelEl)?.focus?.({ preventScroll: true });
        return;
      }
      if (group && current === menu.entries[group.index].labelEl && event.key === 'ArrowRight') {
        if (!group.open) expandGroup();
        group.labelEls.find(el => !el.disabled)?.focus?.({ preventScroll: true });
        return;
      }
      const buttons = menu.entries.map(entry => entry.labelEl).filter(el => !el.disabled);
      let index = buttons.indexOf(current);
      if (index < 0) index = 0;
      if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = buttons.length - 1;
      else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') index = (index + 1) % buttons.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') index = (index - 1 + buttons.length) % buttons.length;
      buttons[index]?.focus?.({ preventScroll: true });
    };
    rootEl.addEventListener('keydown', keydown);

    const click = event => {
      if (!menu || menu !== menuState || menuState.closing) return;
      if (event.target?.closest?.('button')) return;
      const point = menuPoint(event);
      const hit = hitTest(point);
      if (hit.kind === 'hub') { closeMenu(true); return; }
      if (hit.kind === 'segment') { activateItem(menu.entries[hit.index]); return; }
      if (hit.kind === 'sub') { activateGroupChild(menu.group.subEntries[hit.sub]); return; }
      closeMenu(true);
    };
    rootEl.addEventListener('click', click);

    const move = event => {
      if (!menu || menu !== menuState || menuState.closing) return;
      const hit = hitTest(menuPoint(event));
      if (hit.kind === 'segment') {
        if (hit.index === menu.group?.index && menu.activeSub < 0 && menu.activeIndex === hit.index) return;
        setActive(hit.index, -1);
      } else if (hit.kind === 'sub') {
        setActive(hit.index, hit.sub);
      } else {
        setActive(-1, -1);
      }
    };
    rootEl.addEventListener('pointermove', move);
    rootEl.addEventListener('pointerleave', () => {
      if (menu && menu === menuState && !menuState.closing) setActive(-1, -1);
    });
    rootEl.addEventListener('pointerdown', event => {
      if (!menu || menu !== menuState || menuState.closing || event.button !== 2) return;
      // A right-click on the overlay's empty area dismisses the menu and consumes
      // the following contextmenu, so the user does not get a surprise native menu
      // when the node they meant to re-target sits under the open overlay.
      dismissal = { x: event.clientX, y: event.clientY, until: Date.now() + 600 };
      closeMenu(false);
      event.preventDefault();
      event.stopPropagation();
    });
    rootEl.addEventListener('contextmenu', event => event.preventDefault());
  }

  // ── G6 integration ─────────────────────────────────────────────────────────
  function bindingFor(graph) {
    const s = appState();
    return { context: currentContext(), graph, view: s?.graphView, revision: s?.graphView?.revision };
  }

  function openFromEvent({ graph, container, item, kind, event }) {
    dismissal = null;
    const s = appState();
    if (!s || !container) return;
    const context = currentContext();
    if (!context) return;
    const binding = bindingFor(graph);
    const rect = container.getBoundingClientRect();
    const clientX = event.clientX ?? event.originalEvent?.clientX ?? rect.left;
    const clientY = event.clientY ?? event.originalEvent?.clientY ?? rect.top;

    if (kind === 'blank') {
      const spec = blankMenu();
      const created = openMenu({
        spec, radius: DEFAULT_RADIUS, anchor: { x: clientX, y: clientY }, kind,
        model: null, binding, graph, container, item: null,
      });
      installMenuHandlers(created);
      return;
    }

    const model = item.getModel?.() || {};
    let multi = false;
    if (kind === 'node') {
      const ids = selectionIds();
      multi = ids.length > 1 && ids.includes(String(model.id));
    }

    let anchor;
    if (kind === 'node') {
      const zoom = Number(graph.getZoom?.()) || 1;
      const sizeValue = Array.isArray(model.size) ? Math.max(...model.size) : Number(model.size) || 32;
      const projected = graph.getCanvasByPoint?.(Number(model.x), Number(model.y));
      anchor = projected
        ? { x: rect.left + projected.x, y: rect.top + projected.y }
        : { x: clientX, y: clientY };
      if (!multi) {
        try { if (typeof selectItem === 'function') selectItem(item); } catch (_) { /* host state race */ }
      }
      const spec = nodeMenu(item, multi);
      const radius = clamp(sizeValue / 2 * zoom + 42, MIN_RADIUS, MAX_RADIUS);
      const created = openMenu({
        spec, radius, anchor, kind, model, binding,
        graph, container, item,
      });
      installMenuHandlers(created);
      return;
    }

    try {
      if (typeof selectItem === 'function') selectItem(item);
      if (typeof refreshSelectedEdgeAppearance === 'function') refreshSelectedEdgeAppearance();
    } catch (_) { /* host state race */ }
    const spec = edgeMenu(item);
    const created = openMenu({
      spec, radius: DEFAULT_RADIUS, anchor: { x: clientX, y: clientY },
      kind, model, binding, graph, container, item,
    });
    installMenuHandlers(created);
  }

  function openKeyboardMenu({ graph, container }) {
    if (menu) return false;
    const s = appState();
    if (!s || !s.graph) return false;
    const target = doc.activeElement;
    if (target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return false;
    if ([...doc.querySelectorAll('.modal,[role="dialog"],dialog[open]')].some(node => {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.style.display === 'none') return false;
      const style = root.getComputedStyle?.(node);
      return !style || (style.display !== 'none' && style.visibility !== 'hidden');
    })) return false;
    let item = null, kind = 'blank';
    const edgeKey = s.graphView?.selectedEdge;
    if (edgeKey) {
      const edgeItem = s.graphView?.edgeItems?.get(edgeKey);
      if (edgeItem && !edgeItem.destroyed) { item = edgeItem; kind = 'edge'; }
    }
    if (!item) {
      const ids = selectionIds();
      if (ids.length === 1) {
        const node = s.graph.findById?.(ids[0]);
        if (node && !node.destroyed) { item = node; kind = 'node'; }
      }
    }
    const rect = container.getBoundingClientRect();
    const anchor = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    if (item && kind === 'node') {
      const model = item.getModel?.() || {};
      const projected = s.graph.getCanvasByPoint?.(Number(model.x), Number(model.y));
      if (projected) { anchor.x = rect.left + projected.x; anchor.y = rect.top + projected.y; }
    }
    openFromEvent({
      graph, container, item, kind,
      event: { clientX: anchor.x, clientY: anchor.y, originalEvent: null, preventDefault() {}, stopPropagation() {} },
    });
    return true;
  }

  function attach({ graph, container }) {
    if (!graph || !container) return () => {};
    let disposed = false;
    const onContext = event => {
      if (disposed) return;
      const item = event?.item;
      if (item?.destroyed) return;
      try { event.preventDefault?.(); } catch (_) { /* host event */ }
      const source = event.originalEvent;
      try { source?.preventDefault?.(); source?.stopPropagation?.(); } catch (_) { /* host event */ }
      const model = item?.getModel?.() || {};
      const kind = item ? (model.source ? 'edge' : 'node') : 'blank';
      openFromEvent({ graph, container, item: item || null, kind, event });
    };
    const closeForChange = () => { if (menu) closeMenu(true); };
    const onKeyboard = event => {
      if (disposed || event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
      const isContextKey = event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey);
      if (!isContextKey) return;
      event.preventDefault();
      event.stopPropagation();
      openKeyboardMenu({ graph, container });
    };
    graph.on?.('contextmenu', onContext);
    graph.on?.('viewportchange', closeForChange);
    graph.on?.('node:dragstart', closeForChange);
    graph.on?.('edge:dragstart', closeForChange);
    container.addEventListener('keydown', onKeyboard);
    return () => {
      if (disposed) return;
      disposed = true;
      try { graph.off?.('contextmenu', onContext); } catch (_) { /* gone */ }
      try { graph.off?.('viewportchange', closeForChange); } catch (_) { /* gone */ }
      try { graph.off?.('node:dragstart', closeForChange); } catch (_) { /* gone */ }
      try { graph.off?.('edge:dragstart', closeForChange); } catch (_) { /* gone */ }
      try { container.removeEventListener('keydown', onKeyboard); } catch (_) { /* gone */ }
      if (menu) closeMenu(false);
    };
  }

  // ── bootstrap: attach to every G6 instance created by initGraph ───────────
  const registry = new WeakMap();
  function attachCurrent() {
    const s = appState();
    if (!s || !s.graph) return;
    const container = doc.getElementById('graphContainer');
    if (!container || registry.has(s.graph)) return;
    const dispose = attach({ graph: s.graph, container });
    registry.set(s.graph, dispose);
    try { (s.graphDisposers || (s.graphDisposers = [])).push(dispose); } catch (_) { /* host */ }
  }

  if (typeof initGraph === 'function') {
    const originalInitGraph = initGraph;
    // eslint-disable-next-line no-func-assign
    initGraph = function (...args) {
      const result = originalInitGraph.apply(this, args);
      try { attachCurrent(); } catch (_) { /* attach must never break the graph */ }
      return result;
    };
  }
  attachCurrent();
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', attachCurrent, { once: true });

  root.FundsGraphRadialMenu = Object.freeze({
    attach,
    close: () => closeMenu(false),
    isOpen: () => !!menu,
    getState: () => menu ? {
      open: true, kind: menu.kind, count: menu.count,
      active: menu.activeIndex, sub: menu.activeSub,
      groupOpen: !!menu.group?.open,
    } : { open: false },
  });
})(typeof window === 'undefined' ? globalThis : window);
