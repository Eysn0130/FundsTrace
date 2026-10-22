(() => {
  'use strict';
  const surface = document.querySelector('#startup-surface');
  const brand = document.querySelector('.startup-brand');
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  const bridge = window.FundsTraceStartup;
  const accents = { blue: '#367cff', teal: '#14b8a6', violet: '#8b5cf6', rose: '#e75b85', amber: '#e6ad39' };
  const unsubscribeAppearance = bridge.onAppearance?.(value => {
    document.documentElement.dataset.theme = value.theme;
    document.documentElement.dataset.translucency = String(value.translucency);
    document.documentElement.style.setProperty('--startup-accent', accents[value.accent]);
  });
  const clamp = value => Math.max(0, Math.min(1, value));
  const ease = value => { const x = clamp(value); return x*x*x*(x*(x*6-15)+10); };
  const step = (time, start, end) => ease((time-start)/(end-start));
  const FINISH_MS = 1100;
  const REDUCED_FINISH_MS = 140;
  const startedAt = performance.now();
  const rendered = new Map();
  const amber = document.querySelector('.startup-amber-light');
  const animations = [
    document.querySelector('.startup-original-sweep').animate([
      {transform:'translateX(-32%)'}, {transform:'translateX(32%)'},
    ], {duration:1800,delay:120,easing:'cubic-bezier(.17,.6,.22,1)',fill:'both'}),
    amber.animate([{opacity:0},{opacity:.55}], {duration:1300,delay:800,easing:'ease',fill:'both'}),
    amber.animate([{opacity:.55,easing:'ease-in-out'},{opacity:.62,offset:.5,easing:'ease-in-out'},{opacity:.3}], {duration:3600,delay:2100,easing:'linear'}),
    amber.animate([{opacity:.3,easing:'ease-in-out'},{opacity:.62,offset:.5,easing:'ease-in-out'},{opacity:.3}], {duration:3600,delay:5700,easing:'linear',iterations:Infinity}),
  ];
  for (const animation of animations) { animation.pause(); animation.currentTime = 0; }
  let phase = 'waiting';
  let reduced = media.matches;
  let handoffAt = 0;
  let finishDuration = FINISH_MS;
  let raf = 0;
  let unsubscribe = () => {};

  function property(name, value, unit = '') {
    const text = `${value.toFixed(5)}${unit}`;
    if (rendered.get(name) === text) return;
    rendered.set(name, text);
    surface.style.setProperty(`--${name}`, text);
  }

  function resize() {
    if (phase === 'complete' || phase === 'disposed') return;
    const scale = Math.max(0, Math.min(1, (surface.clientWidth-48)/brand.offsetWidth, (surface.clientHeight-48)/brand.offsetHeight));
    property('brand-scale', scale);
  }
  const observer = new ResizeObserver(resize);
  observer.observe(surface);
  // Font resolution may change intrinsic height without changing the viewport.
  observer.observe(brand);

  function renderWaiting(now) {
    const elapsed = Math.max(0, now-startedAt);
    property('backdrop-opacity', reduced ? 1 : 1-.4*step(elapsed,1400,6500));
    property('wait-opacity', reduced ? .75 : .75*step(elapsed,400,850));
  }

  function renderMotion(now) {
    const elapsed = Math.max(0, now-startedAt);
    property('wait-x', reduced ? 10 : -15+50*((elapsed%2150)/2150), 'px');
    if (!reduced) for (const animation of animations) animation.currentTime = elapsed;
  }

  function cleanup() {
    cancelAnimationFrame(raf);
    observer.disconnect();
    unsubscribe();
    unsubscribeAppearance?.();
    media.removeEventListener('change', motionChanged);
    for (const animation of animations) animation.cancel();
  }

  function tick(now) {
    if (phase === 'complete' || phase === 'disposed') return;
    if (phase === 'waiting') renderWaiting(now);
    else {
      const elapsed = Math.max(0, now-handoffAt);
      property('exit-opacity', 1-step(elapsed/finishDuration,.04,.96));
      if (elapsed >= finishDuration) {
        phase = 'complete';
        surface.dataset.phase = phase;
        cleanup();
        bridge.complete();
        return;
      }
    }
    renderMotion(now);
    raf = requestAnimationFrame(tick);
  }

  function beginHandoff() {
    if (phase !== 'waiting') return;
    const now = performance.now();
    // Capture the waiting state at the actual signal time, including early readiness.
    renderWaiting(now);
    renderMotion(now);
    handoffAt = now;
    finishDuration = reduced ? REDUCED_FINISH_MS : FINISH_MS;
    phase = 'handoff';
    surface.dataset.phase = phase;
  }

  function motionChanged() {
    // Keep an in-flight handoff on its original duration and curve.
    if (phase !== 'waiting') return;
    reduced = media.matches;
    document.documentElement.dataset.motion = reduced ? 'reduce' : 'full';
    renderWaiting(performance.now());
    renderMotion(performance.now());
  }

  document.documentElement.dataset.motion = reduced ? 'reduce' : 'full';
  renderWaiting(startedAt);
  renderMotion(startedAt);
  resize();
  media.addEventListener('change', motionChanged);
  unsubscribe = bridge.onReady(beginHandoff);
  window.addEventListener('pagehide', () => {
    if (phase === 'complete' || phase === 'disposed') return;
    phase = 'disposed';
    cleanup();
  }, {once:true});
  raf = requestAnimationFrame(tick);
})();
