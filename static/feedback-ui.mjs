import { createFeedbackStore } from './feedback-core.mjs';

const store = createFeedbackStore();
const STATUS = { queued: '等待中', running: '进行中', unknown: '待核对', success: '已完成', warning: '需留意', error: '未完成', cancelled: '已取消' };
const SEVERITY = { success: '成功', info: '提示', warning: '注意', error: '错误' };
let expanded = false, root, dock, dockBody, taskList, entryToggle, toggle, summary, toastStack, polite, assertive;
let unsubscribe, observer, resizeObserver, disposed = false, blocked = false, externalModalBlocked = false;
const toastNodes = new Map(), taskNodes = new Map(), announced = new Set(), cleanup = [];

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
    if (toastStack.hidden !== blocked) toastStack.hidden = blocked;
    // The legacy dialog manager also restores sibling inert state. Reconcile
    // this root after either observer order so that it cannot stay inert.
    if (root.inert !== blocked) root.inert = blocked;
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
  const failed = () => store.notify({ scope: item.scope, severity: 'error', title: '操作未完成', detail: '请核对当前任务状态后再试。' });
  try {
    const result = store.runAction(item.id, action.id);
    if (result && typeof result.then === 'function') result.catch(failed);
  } catch { failed(); }
}

function makeRow(item, toast) {
  const node = element(toast ? 'article' : 'li', toast ? 'ff-toast' : 'ff-task');
  node.dataset.feedbackId = item.id;
  const content = element('div', 'ff-content');
  const heading = element('div', 'ff-item-heading');
  const state = element('span', 'ff-state');
  const stateIcon = icon('check');
  const stateLabel = element('span');
  state.append(stateIcon, stateLabel);
  const title = element('span', 'ff-title');
  const detail = element('p', 'ff-detail');
  const progress = element('progress', 'ff-progress');
  const progressLabel = element('span', 'ff-progress-label');
  const actions = element('div', 'ff-actions');
  const close = element('button', 'ff-close');
  close.type = 'button'; close.append(icon('x'));
  heading.append(state, title); content.append(heading, detail, progress, progressLabel, actions);
  node.append(content, close);
  close.addEventListener('click', () => store.dismiss(item.id));
  node._feedback = { stateIcon, stateLabel, title, detail, progress, progressLabel, actions, close, actionNodes: new Map() };
  updateRow(node, item, toast);
  return node;
}

function updateRow(node, item, toast) {
  const parts = node._feedback, status = item.status || item.severity;
  node.dataset.status = status;
  parts.stateIcon.hidden = status !== 'success';
  parts.stateLabel.textContent = item.kind === 'task' ? STATUS[status] : SEVERITY[status];
  parts.title.textContent = item.title + (toast && item.count > 1 ? ` · ${item.count}` : '');
  parts.detail.textContent = item.detail;
  parts.detail.hidden = !item.detail;
  const running = item.kind === 'task' && item.status === 'running';
  const determinate = running && item.progress;
  parts.progress.hidden = !running;
  parts.progressLabel.hidden = !running;
  parts.progress.setAttribute('aria-label', `${item.title}进度`);
  if (determinate) {
    parts.progress.max = item.progress.total; parts.progress.value = item.progress.done;
    parts.progressLabel.textContent = `${item.progress.done} / ${item.progress.total}`;
  } else if (running) {
    parts.progress.removeAttribute('value'); parts.progress.removeAttribute('max');
    parts.progressLabel.textContent = '正在处理';
  }
  parts.close.hidden = !toast && item.kind !== 'task';
  parts.close.setAttribute('aria-label', toast ? `关闭提示：${item.title}` : `关闭任务展示：${item.title}（不取消任务）`);
  parts.close.title = toast ? '关闭提示' : '仅关闭展示，不取消任务';
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
  parts.actions.hidden = !item.actions.length;
}

function reconcile(container, nodes, items, toast = false) {
  const ids = new Set(items.map(item => item.id));
  const entering = items.some(item => !nodes.has(item.id));
  for (const [id, node] of nodes) if (!ids.has(id)) {
    nodes.delete(id);
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (toast && !entering && !blocked && !reduced && typeof node.animate === 'function') {
      const animation = node.animate([{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-4px)' }], { duration: 140, easing: 'ease-out' });
      animation.finished.then(() => node.remove(), () => node.remove());
    } else node.remove();
  }
  for (const item of items) {
    let node = nodes.get(item.id);
    if (!node) { node = makeRow(item, toast); nodes.set(item.id, node); }
    else updateRow(node, item, toast);
    // insertBefore only moves changed positions, preserving focused actions.
    const index = items.indexOf(item), current = container.children[index];
    if (current !== node) container.insertBefore(node, current || null);
  }
}

function layoutChanged() {
  if (!root || disposed) return;
  const height = dock.getBoundingClientRect().height;
  root.style.setProperty('--feedback-dock-height', `${height}px`);
  window.dispatchEvent(new CustomEvent('funds:feedback-layout', { detail: { height } }));
}

function render() {
  if (!root || disposed) return;
  syncModal();
  const state = store.snapshot();
  const running = state.tasks.filter(item => item.status === 'running' || item.status === 'queued').length;
  const unknown = state.tasks.filter(item => item.status === 'unknown').length;
  const attention = state.tasks.filter(item => ['warning', 'error'].includes(item.status)).length;
  summary.textContent = [running ? `${running} 项进行中` : '', unknown ? `${unknown} 项待核对` : '', attention ? `${attention} 项需留意` : ''].filter(Boolean).join(' · ') || '暂无进行中的任务';
  dock.dataset.expanded = String(expanded);
  toggle.setAttribute('aria-expanded', String(expanded));
  entryToggle.setAttribute('aria-expanded', String(expanded));
  toggle.querySelector('.ff-toggle-label').textContent = expanded ? '收起' : '展开';
  dockBody.inert = !expanded;
  dockBody.setAttribute('aria-hidden', String(!expanded));
  const entries = [...state.tasks.filter(item => !item.dismissed), ...state.activity.filter(item => item.kind === 'notification')];
  reconcile(taskList, taskNodes, entries);
  taskList.hidden = entries.length === 0;
  dockBody.querySelector('.ff-empty').hidden = entries.length !== 0;
  reconcile(toastStack, toastNodes, state.toasts, true);
  // Removing a focused element need not dispatch focusout in browsers.
  if (!toastStack.contains(document.activeElement)) store.resume('focus');
  if (!state.toasts.length) store.resume('hover');
  announce(state.toasts);
  layoutChanged();
}

function openTasks() {
  expanded = true;
  store.revealTasks();
  render();
}

function mount() {
  if (disposed || root || new URLSearchParams(window.location.search).get('embedded') === '1') return;
  root = element('div', 'funds-feedback ff-root');
  root.dataset.feedbackHost = 'static';
  toastStack = element('div', 'ff-toast-stack'); toastStack.setAttribute('aria-label', '操作提示');
  dock = element('section', 'ff-dock'); dock.setAttribute('aria-label', '任务与活动');
  const head = element('div', 'ff-dock-head');
  const entry = element('button', 'ff-dock-entry'); entry.type = 'button';
  entryToggle = entry;
  entry.append(icon('list'), element('span', 'ff-dock-title', '任务与活动'));
  summary = element('span', 'ff-summary'); entry.append(summary);
  toggle = element('button', 'ff-dock-toggle'); toggle.type = 'button';
  toggle.append(element('span', 'ff-toggle-label', '展开'), icon('chevron-up'));
  const bodyId = 'fundsFeedbackTaskBody';
  entry.setAttribute('aria-controls', bodyId); toggle.setAttribute('aria-controls', bodyId);
  dockBody = element('div', 'ff-dock-body'); dockBody.id = bodyId;
  taskList = element('ol', 'ff-task-list'); taskList.setAttribute('aria-label', '任务和重要活动记录');
  dockBody.append(taskList, element('p', 'ff-empty', '任务进度、结果待核对和重要提示会保留在这里。'));
  head.append(entry, toggle); dock.append(head, dockBody);
  polite = element('div', 'ff-sr'); polite.setAttribute('role', 'status'); polite.setAttribute('aria-live', 'polite'); polite.setAttribute('aria-atomic', 'true');
  assertive = element('div', 'ff-sr'); assertive.setAttribute('role', 'alert'); assertive.setAttribute('aria-atomic', 'true');
  root.append(toastStack, dock, polite, assertive); document.body.append(root);
  const toggleOpen = () => { expanded = !expanded; if (expanded) store.revealTasks(); render(); };
  listen(entry, 'click', toggleOpen); listen(toggle, 'click', toggleOpen);
  listen(toastStack, 'pointerenter', () => store.pause('hover'));
  listen(toastStack, 'pointerleave', () => store.resume('hover'));
  listen(toastStack, 'focusin', () => store.pause('focus'));
  listen(toastStack, 'focusout', () => queueMicrotask(() => { if (!toastStack.contains(document.activeElement)) store.resume('focus'); }));
  const visibility = () => { if (document.hidden) store.pause('hidden'); else { store.resume('hidden'); render(); } };
  listen(document, 'visibilitychange', visibility); visibility();
  observer = new MutationObserver(() => { if (syncModal() === false) announce(store.snapshot().toasts); });
  observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open', 'style', 'hidden', 'inert'], childList: true });
  if (typeof ResizeObserver === 'function') { resizeObserver = new ResizeObserver(layoutChanged); resizeObserver.observe(dock); }
  unsubscribe = store.subscribe(render);
  render();
}

function dispose() {
  if (disposed) return;
  disposed = true;
  unsubscribe?.(); observer?.disconnect(); resizeObserver?.disconnect();
  for (const remove of cleanup) remove();
  root?.remove(); store.dispose();
}

const feedback = Object.freeze({ ...store, openTasks, setModalBlocked, dispose });
window.FundsFeedback = feedback;
window.dispatchEvent(new CustomEvent('funds:feedback-ready', { detail: feedback }));
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
else mount();

export { feedback };
