(function (root) {
  'use strict';
  const bridge = root.FundsDesktopUpdates;
  if (!bridge || !root.customElements || root.customElements.get('funds-desktop-update')) return;
  const phases = {
    available: ['更新', 'download'],
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
        if (action) void bridge[action]().then(value => this.render(value)).catch(() => this.render({ ...this.state, phase: 'error' }));
      });
      this.unsubscribe = bridge.subscribe(value => { this.received = true; this.render(value); });
      void bridge.state().then(value => { if (!this.received) this.render(value); }).catch(() => this.render(null));
      this.feedbackReady = () => this.feedback(this.state);
      root.addEventListener?.('funds:feedback-ready', this.feedbackReady);
    }
    disconnectedCallback() { this.unsubscribe?.(); root.removeEventListener?.('funds:feedback-ready', this.feedbackReady); }
    feedback(value) {
      if (!root.FundsFeedback || !this.isConnected || !value) return;
      const details = {
        checking: ['running', '正在检查更新', '正在读取更新信息，完成时间暂无法确定。'],
        available: ['warning', '有可用更新', `FundsTrace ${value.version || ''} 可下载。`],
        downloading: ['running', '正在下载更新', `正在下载 FundsTrace ${value.version || ''}。`],
        ready: ['warning', '更新已准备好', '下载已完成，重启后继续安装。'],
        installing: ['running', '正在重启以更新', '安装结果将在下次启动后确认。'],
        current: ['success', '更新检查完成', `当前已是最新版本${value.currentVersion ? ' ' + value.currentVersion : ''}。`],
        error: ['error', '更新未完成', '请重试检查更新，或从官网下载最新版覆盖安装。'],
      };
      const state = details[value.phase];
      if (!state) return;
      const action = phases[value.phase]?.[1];
      const signature = JSON.stringify([value.phase, value.currentVersion, value.version, value.percent]);
      if (signature === this.feedbackSignature && this.feedbackStore === root.FundsFeedback) return;
      this.feedbackSignature = signature; this.feedbackStore = root.FundsFeedback;
      const progress = Number(value.percent);
      root.FundsFeedback.task({ id: 'desktop:update', scope: null, operation: 'download',
        status: state[0], title: state[1], detail: state[2], announce: false,
        ...(value.phase === 'downloading' && Number.isFinite(progress) ? { progress: { done: Math.max(0, Math.min(100, progress)), total: 100 } } : {}),
        actions: action ? [{ id: action, label: phases[value.phase][0], run: async () => {
          if (!this.isConnected || phases[this.state?.phase]?.[1] !== action) return;
          try { this.render(await bridge[action]()); }
          catch (_) { this.render({ ...this.state, phase: 'error' }); }
        } }] : [],
      });
    }
    render(value) {
      if (!this.isConnected) return;
      this.state = value;
      this.feedback(value);
      const config = phases[value?.phase]; this.hidden = !config || !value.version;
      this.button.dataset.phase = value?.phase || '';
      if (this.hidden) { this.label.textContent = ''; this.status.textContent = ''; return; }
      const downloading = value.phase === 'downloading';
      const percent = Math.floor(Math.max(0, Math.min(100, Number(value.percent) || 0)));
      const label = downloading ? `${percent}%` : config[0];
      this.label.textContent = label;
      this.button.disabled = !config[1];
      this.ring.style.setProperty('--funds-update-progress', `${percent}%`);
      this.button.title = value.phase === 'error' ? '更新未完成，点击重试。也可从 analytix.top 下载最新版覆盖安装。'
        : `FundsTrace ${value.currentVersion || ''}${value.version ? ` → ${value.version}` : ''}`;
      this.button.setAttribute('aria-label', downloading ? `正在下载更新 ${percent}%` : config[0]);
      // Avoid announcing every progress tick to screen readers.
      if (!downloading) this.status.textContent = config[0];
    }
  });
})(window);
