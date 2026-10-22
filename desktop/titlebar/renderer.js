(() => {
  'use strict';
  const accents = { blue: '#367cff', teal: '#14b8a6', violet: '#8b5cf6', rose: '#e75b85', amber: '#e6ad39' };
  const unsubscribe = window.FundsTraceTitlebar.onAppearance(value => {
    document.documentElement.dataset.theme = value.theme;
    document.documentElement.dataset.translucency = String(value.translucency);
    document.documentElement.style.setProperty('--caption-accent', accents[value.accent]);
  });
  window.addEventListener('pagehide', () => unsubscribe(), { once: true });
})();
