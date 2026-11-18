import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const apiPort = process.env.FUNDS_TRACE_DEV_API_PORT || '8300';
if (!/^[0-9]{1,5}$/.test(apiPort) || Number(apiPort) < 1 || Number(apiPort) > 65535) {
  throw new Error('FUNDS_TRACE_DEV_API_PORT must be a loopback port from 1 to 65535');
}
const backend = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  plugins: [react()],
  base: '/workspace/',
  server: {
    host: '127.0.0.1',
    proxy: {
      '/workspace/assets': backend,
      '/api': backend,
      '/static': backend,
      '/license': backend,
      '^/(?:\\?.*)?$': backend,
      '^/analysis(?:\\?.*)?$': backend,
    },
  },
  build: { target: 'es2022', rollupOptions: {
    preserveEntrySignatures: 'strict',
    input: { main: fileURLToPath(new URL('./index.html', import.meta.url)), 'import-entry': fileURLToPath(new URL('./src/standalone/import-entry.tsx', import.meta.url)) },
    output: { entryFileNames: chunk => chunk.name === 'import-entry' ? 'assets/import-entry.js' : 'assets/[name]-[hash].js' },
  } },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'] },
});
