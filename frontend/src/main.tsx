import { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

// Development-only host fixture. The production bundle has no fixture branch.
const HostPreview = import.meta.env.DEV && window.location.pathname === '/workspace/host-preview/'
  ? lazy(() => import('./host-demo/HostPreview')) : null;
createRoot(document.getElementById('root')!).render(HostPreview
  ? <Suspense fallback={<p>正在加载合成宿主…</p>}><HostPreview /></Suspense>
  : <App />);
