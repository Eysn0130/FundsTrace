import { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

// Development-only host fixture. The production bundle has no fixture branch.
const HostPreview = import.meta.env.DEV && window.location.pathname === '/workspace/host-preview/'
  ? lazy(() => import('./host-demo/HostPreview')) : null;
// The legacy root owns the persistent G6 canvas and the shared Windows shell.
// Direct entry must join that host instead of creating a second workspace.
const embedded = new URLSearchParams(window.location.search).get('embedded') === '1' && window.parent !== window;
if (!HostPreview && !embedded) {
  const destination = new URL('/', window.location.href);
  destination.searchParams.set('page', new URLSearchParams(window.location.search).get('surface') === 'accounts' ? 'accounts' : 'cases');
  const caseId = new URLSearchParams(window.location.search).get('case_id');
  if (caseId) destination.searchParams.set('case_id', caseId);
  window.location.replace(destination);
} else createRoot(document.getElementById('root')!).render(HostPreview
  ? <Suspense fallback={<p>正在加载合成宿主…</p>}><HostPreview /></Suspense>
  : <App />);
