/* UI decisions only. Dismissal never cancels or replays a business operation. */
(function (root) {
  'use strict';
  let pending = null;
  function confirm(options) {
    pending?.finish(false);
    if (options.scope && !isCurrentCase(options.scope)) return Promise.resolve(false);
    const trigger = document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.className = 'action-dialog feedback-confirm';
    const title = document.createElement('h2'); title.id = 'feedbackConfirmTitle'; title.textContent = options.title;
    const message = document.createElement('p'); message.id = 'feedbackConfirmMessage'; message.textContent = options.message;
    const footer = document.createElement('footer'), cancel = document.createElement('button'), accept = document.createElement('button');
    cancel.type = accept.type = 'button'; cancel.className = 'btn'; cancel.textContent = '取消';
    accept.className = options.danger ? 'btn danger' : 'btn primary'; accept.textContent = options.accept || '确认';
    dialog.setAttribute('aria-labelledby', title.id); dialog.setAttribute('aria-describedby', message.id);
    footer.append(cancel, accept); dialog.append(title, message, footer); document.body.appendChild(dialog);
    return new Promise(resolve => {
      const owner = { finish(value) {
        if (pending !== owner) return;
        pending = null; root.removeEventListener('funds:feedback-binding', revoke);
        if (dialog.open) dialog.close(); dialog.remove();
        if (trigger?.isConnected && !trigger.closest('[inert]') && !trigger.disabled) trigger.focus({ preventScroll: true });
        resolve(value === true && (!options.scope || isCurrentCase(options.scope)));
      } };
      const revoke = () => owner.finish(false);
      pending = owner;
      cancel.onclick = () => owner.finish(false); accept.onclick = () => owner.finish(true);
      dialog.addEventListener('cancel', event => { if (event.target === dialog) { event.preventDefault(); owner.finish(false); } });
      dialog.addEventListener('close', () => owner.finish(false));
      dialog.addEventListener('keydown', event => { if (event.isComposing && ['Escape', 'Enter'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); } });
      root.addEventListener('funds:feedback-binding', revoke);
      dialog.showModal(); cancel.focus({ preventScroll: true });
    });
  }
  root.FundsFeedbackDialogs = Object.freeze({ confirm, revoke: () => pending?.finish(false) });
})(window);
