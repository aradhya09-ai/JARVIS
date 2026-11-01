import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dashboard is served either by Vite on :5173 (dev) or by FastAPI on :8765.
// The backend rejects unknown origins, so the dev server stays on loopback.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    target: 'es2022',
    sourcemap: false,
  },
});
