(function (root) {
  'use strict';
  const bridge = root.FundsDesktopUpdates;
  if (!bridge || !root.customElements || root.customElements.get('funds-desktop-update')) return;
  const phases = {
    idle: ['检查更新', 'check'], current: ['已是最新版', 'check'],
    checking: ['检查中', null], available: ['更新', 'download'],
    downloading: ['下载中', null], ready: ['重启更新', 'install'],
    installing: ['正在重启', null], error: ['重试更新', 'check'],
  };
  root.customElements.define('funds-desktop-update', class extends HTMLElement {
    connectedCallback() {
      this.button = document.createElement('button');
      this.button.type = 'button'; this.button.className = 'funds-update-capsule';
      this.ring = document.createElement('span'); this.ring.className = 'funds-update-ring';
      this.ring.setAttribute('aria-hidden', 'true');
      this.label = document.createElement('span');
      this.button.append(this.ring, this.label); this.replaceChildren(this.button);
      this.status = document.createElement('span'); this.status.className = 'funds-update-status';
      this.status.setAttribute('role', 'status'); this.append(this.status);
      this.button.addEventListener('click', () => {
        const action = phases[this.state?.phase]?.[1];
        if (action) void bridge[action]().then(value => this.render(value)).catch(() => this.render({ phase: 'error' }));
      });
      this.unsubscribe = bridge.subscribe(value => { this.received = true; this.render(value); });
      void bridge.state().then(value => { if (!this.received) this.render(value); }).catch(() => this.render(null));
    }
    disconnectedCallback() { this.unsubscribe?.(); }
    render(value) {
      if (!this.isConnected) return;
      this.state = value;
      const config = phases[value?.phase]; this.hidden = !config;
      if (!config) return;
      const downloading = value.phase === 'downloading';
      const percent = Math.floor(Math.max(0, Math.min(100, Number(value.percent) || 0)));
      const label = downloading ? `${percent}%` : config[0];
      this.label.textContent = label;
      this.button.disabled = !config[1];
      this.button.dataset.phase = value.phase;
      this.ring.style.setProperty('--funds-update-progress', `${percent}%`);
      this.button.title = value.phase === 'error' ? '更新未完成，点击重试。也可从 analytix.top 下载最新版覆盖安装。'
        : `FundsTrace ${value.currentVersion || ''}${value.version ? ` → ${value.version}` : ''}`;
      this.button.setAttribute('aria-label', downloading ? `正在下载更新 ${percent}%` : config[0]);
      // Avoid announcing every progress tick to screen readers.
      if (!downloading) this.status.textContent = config[0];
    }
  });
})(window);
