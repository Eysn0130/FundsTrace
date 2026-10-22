(() => {
  'use strict';
  const menu = document.querySelector('#titlebar-menu');
  const bridge = window.FundsTraceTitlebar;
  if (!menu || !bridge || typeof bridge.openMenu !== 'function' || typeof bridge.onMenuEnabled !== 'function') return;

  const unsubscribe = bridge.onMenuEnabled(enabled => {
    if (typeof enabled === 'boolean') menu.disabled = !enabled;
  });
  menu.addEventListener('click', () => {
    if (!menu.disabled) bridge.openMenu();
  });
  window.addEventListener('pagehide', () => unsubscribe(), { once: true });
})();
