(function () {
  'use strict';

  function bind({ viewport, range, left, right, table }) {
    if (!viewport || !range || !left || !right) return;
    let idleTimer = null, active = true;
    const limit = () => Math.max(0, (viewport.scrollWidth || 0) - (viewport.clientWidth || 0));
    const sync = () => {
      if (!active) return;
      const max = limit(), value = Math.max(0, Math.min(max, viewport.scrollLeft || 0));
      range.max = String(max); range.value = String(value); range.disabled = max === 0;
      left.disabled = value <= 0; right.disabled = value >= max;
      range.setAttribute('aria-valuetext', max ? `水平位置 ${Math.round(value)} / ${Math.round(max)}` : '全部列已显示');
      range.style.setProperty?.('--flow-scroll-progress', `${max ? value / max * 100 : 0}%`);
    };
    const move = value => { if (!active) return; viewport.scrollLeft = Math.max(0, Math.min(limit(), Number(value) || 0)); sync(); };
    const page = () => Math.max(32, Math.round((viewport.clientWidth || 0) * .7));
    const scroll = () => {
      if (!active) return;
      sync(); viewport.classList.add('is-scrolling'); clearTimeout(idleTimer);
      idleTimer = setTimeout(() => viewport.classList.remove('is-scrolling'), 800);
    };
    const key = event => {
      const max = limit(), value = viewport.scrollLeft || 0;
      const next = {ArrowLeft:value-24,ArrowRight:value+24,PageUp:value-page(),PageDown:value+page(),Home:0,End:max}[event.key];
      if (next === undefined) return;
      event.preventDefault(); move(next);
    };
    const input = () => move(range.value), moveLeft = () => move((viewport.scrollLeft || 0) - page()), moveRight = () => move((viewport.scrollLeft || 0) + page());
    range.oninput = input;
    range.addEventListener('keydown', key);
    left.onclick = moveLeft; right.onclick = moveRight;
    viewport.addEventListener('scroll', scroll, {passive:true});
    window.addEventListener('resize', sync);
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(sync) : null;
    observer?.observe(viewport); if (table) observer?.observe(table);
    const destroy = () => {
      if (!active) return;
      active = false;
      clearTimeout(idleTimer); observer?.disconnect();
      viewport.removeEventListener('scroll', scroll); window.removeEventListener('resize', sync);
      range.removeEventListener('keydown', key);
      if (range.oninput === input) range.oninput = null;
      if (left.onclick === moveLeft) left.onclick = null;
      if (right.onclick === moveRight) right.onclick = null;
      viewport.classList.remove('is-scrolling');
    };
    sync();
    return { sync, destroy };
  }

  window.FundsTableScroll = { bind };
})();
