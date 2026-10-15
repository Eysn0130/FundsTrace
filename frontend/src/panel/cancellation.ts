/** Abort propagation is synchronous; React cleanup only releases the listener. */
export function linkAbortSignal(parent: AbortSignal, controller: AbortController): () => void {
  const abort = () => controller.abort();
  if (parent.aborted) abort();
  else parent.addEventListener('abort', abort, { once: true });
  return () => parent.removeEventListener('abort', abort);
}

export function runUnlessAborted<T>(signal: AbortSignal, operation: () => Promise<T>): Promise<T> {
  if (signal.aborted) return Promise.reject(new DOMException('案件会话已撤销。', 'AbortError'));
  try { return operation(); }
  catch (error) { return Promise.reject(error); }
}

export function waitForNextPoll(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('查询已中止。', 'AbortError')); return; }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, delay);
    const abort = () => { clearTimeout(timer); reject(new DOMException('查询已中止。', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
  });
}
