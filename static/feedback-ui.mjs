import { createFeedbackStore } from './feedback-core.mjs';

const store = createFeedbackStore();
const SEVERITY = { success: '成功', info: '提示', warning: '注意', error: '错误' };
let root, toastStack, polite, assertive;
let unsubscribe, observer, disposed = false, blocked = false, externalModalBlocked = false;
const toastNodes = new Map(), announced = new Set(), cleanup = [];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function listen(target, name, fn, options) {
  target.addEventListener(name, fn, options);
  cleanup.push(() => target.removeEventListener(name, fn, options));
}

function setFlag(node, name, value) {
  const next = Boolean(value);
  if (node[name] !== next) node[name] = next;
}

function icon(name) {
  const image = element('img', 'ff-icon');
  image.src = `/static/ui-icons/${name}.svg`; image.alt = ''; image.setAttribute('aria-hidden', 'true');
  return image;
}

function isModalOpen() {
  try { if (document.querySelector('dialog:modal')) return true; }
  catch { if (document.querySelector('dialog[open]')) return true; }
  return [...(document.querySelectorAll?.('.modal') || [])].some(modal => {
    if (!modal.isConnected || modal.hidden) return false;
    const style = window.getComputedStyle(modal);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
}

function syncModal() {
  const next = externalModalBlocked || isModalOpen();
  if (next !== blocked) {
    blocked = next;
    if (blocked) store.pause('dialog');
    else store.resume('dialog');
  }
  if (root) {
    if (blocked && root.getAttribute('aria-hidden') !== 'true') root.setAttribute('aria-hidden', 'true');
    else if (!blocked && root.getAttribute('aria-hidden') !== null) root.removeAttribute('aria-hidden');
    setFlag(toastStack, 'hidden', blocked);
    if (toastStack.hidden) { store.resume('hover'); store.resume('focus'); }
    // The legacy dialog manager also restores sibling inert state. Reconcile
    // this root after either observer order so that it cannot stay inert.
    setFlag(root, 'inert', blocked);
  }
  return blocked;
}

function setModalBlocked(value) {
  if (disposed) return;
  externalModalBlocked = value === true;
  if (!syncModal() && root) announce(store.snapshot().toasts);
}

function announce(items) {
  if (blocked || document.hidden) return;
  const visible = new Set(items.map(item => item.id));
  for (const id of announced) if (!visible.has(id)) announced.delete(id);
  const fresh = items.filter(item => !announced.has(item.id));
  for (const item of fresh) announced.add(item.id);
  const messages = severity => fresh.filter(item => severity(item.severity)).map(item => [item.title, item.detail].filter(Boolean).join('。')).join('；');
  const normal = messages(value => value !== 'error'), errors = messages(value => value === 'error');
  if (normal) polite.textContent = normal;
  if (errors) assertive.textContent = errors;
}

function run(item, action) {
  if (disposed || syncModal()) return;
  const failed = () => store.notify({ scope: item.scope, severity: 'error', title: '操作未完成', detail: '请核对当前任务状态后再试。' });
  try {
    const result = store.runAction(item.id, action.id);
    if (result && typeof result.then === 'function') result.catch(failed);
  } catch { failed(); }
}

function makeRow(item) {
  const node = element('article', 'ff-toast');
  node.dataset.feedbackId = item.id;
  const content = element('div', 'ff-content');
  const heading = element('div', 'ff-item-heading');
  const state = element('span', 'ff-state');
  const stateIcon = icon('check');
  const stateLabel = element('span');
  state.append(stateIcon, stateLabel);
  const title = element('span', 'ff-title');
  const detail = element('p', 'ff-detail');
  const actions = element('div', 'ff-actions');
  const close = element('button', 'ff-close');
  close.type = 'button'; close.append(icon('x'));
  heading.append(state, title); content.append(heading, detail, actions);
  node.append(content, close);
  close.addEventListener('click', () => store.dismiss(item.id));
  node._feedback = { stateIcon, stateLabel, title, detail, actions, close, actionNodes: new Map() };
  updateRow(node, item);
  return node;
}

function updateRow(node, item) {
  const parts = node._feedback, status = item.severity;
  node.dataset.status = status;
  setFlag(parts.stateIcon, 'hidden', status !== 'success');
  parts.stateLabel.textContent = SEVERITY[status];
  parts.title.textContent = item.title + (item.count > 1 ? ` · ${item.count}` : '');
  parts.detail.textContent = item.detail;
  setFlag(parts.detail, 'hidden', !item.detail);
  parts.close.setAttribute('aria-label', `关闭提示：${item.title}`);
  parts.close.title = '关闭提示';
  const present = new Set(item.actions.map(action => action.id));
  for (const [id, button] of parts.actionNodes) if (!present.has(id)) { button.remove(); parts.actionNodes.delete(id); }
  for (const action of item.actions) {
    let button = parts.actionNodes.get(action.id);
    if (!button) {
      button = element('button', 'ff-action'); button.type = 'button';
      parts.actionNodes.set(action.id, button); parts.actions.append(button);
    }
    button.textContent = action.label;
    button.onclick = () => run(item, action);
  }
  setFlag(parts.actions, 'hidden', !item.actions.length);
}

function reconcile(container, nodes, items) {
  const ids = new Set(items.map(item => item.id));
  const entering = items.some(item => !nodes.has(item.id));
  for (const [id, node] of nodes) if (!ids.has(id)) {
    nodes.delete(id);
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!entering && !blocked && !reduced && typeof node.animate === 'function') {
      const animation = node.animate([{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-4px)' }], { duration: 140, easing: 'ease-out' });
      animation.finished.then(() => node.remove(), () => node.remove());
    } else node.remove();
  }
  for (const item of items) {
    let node = nodes.get(item.id);
    if (!node) { node = makeRow(item); nodes.set(item.id, node); }
    else updateRow(node, item);
    // insertBefore only moves changed positions, preserving focused actions.
    const index = items.indexOf(item), current = container.children[index];
    if (current !== node) container.insertBefore(node, current || null);
  }
}

function render() {
  if (!root || disposed) return;
  syncModal();
  const state = store.snapshot();
  reconcile(toastStack, toastNodes, state.toasts);
  // Removing a focused element need not dispatch focusout in browsers.
  if (!toastStack.contains(document.activeElement)) store.resume('focus');
  if (!state.toasts.length) store.resume('hover');
  announce(state.toasts);
}

function mount() {
  if (disposed || root || new URLSearchParams(window.location.search).get('embedded') === '1') return;
  root = element('div', 'funds-feedback ff-root');
  root.dataset.feedbackHost = 'static';
  toastStack = element('div', 'ff-toast-stack'); toastStack.setAttribute('aria-label', '操作提示');
  polite = element('div', 'ff-sr'); polite.setAttribute('role', 'status'); polite.setAttribute('aria-live', 'polite'); polite.setAttribute('aria-atomic', 'true');
  assertive = element('div', 'ff-sr'); assertive.setAttribute('role', 'alert'); assertive.setAttribute('aria-atomic', 'true');
  root.append(toastStack, polite, assertive); document.body.append(root);
  listen(toastStack, 'pointerenter', () => store.pause('hover'));
  listen(toastStack, 'pointerleave', () => store.resume('hover'));
  listen(toastStack, 'focusin', () => store.pause('focus'));
  listen(toastStack, 'focusout', () => queueMicrotask(() => { if (!toastStack.contains(document.activeElement)) store.resume('focus'); }));
  const visibility = () => { if (document.hidden) store.pause('hidden'); else { store.resume('hidden'); render(); } };
  listen(document, 'visibilitychange', visibility); visibility();
  observer = new MutationObserver(() => { if (syncModal() === false) announce(store.snapshot().toasts); });
  observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open', 'style', 'hidden', 'inert'], childList: true });
  unsubscribe = store.subscribe(render);
  render();
  window.dispatchEvent(new CustomEvent('funds:feedback-layout', { detail: { height: 0 } }));
}

function dispose() {
  if (disposed) return;
  disposed = true;
  unsubscribe?.(); observer?.disconnect();
  for (const remove of cleanup) remove();
  root?.remove(); store.dispose();
}

const feedback = Object.freeze({ ...store, setModalBlocked, dispose });
window.FundsFeedback = feedback;
window.dispatchEvent(new CustomEvent('funds:feedback-ready', { detail: feedback }));
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
else mount();

export { feedback };
