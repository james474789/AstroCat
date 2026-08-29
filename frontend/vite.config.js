import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

// Determine the environment directory:
// If .env exists in the parent directory, we are likely in local development.
// Otherwise, we use the current directory (for Docker builds where .env is injected).
const envDir = fs.existsSync(path.resolve(__dirname, '../.env'))
  ? path.resolve(__dirname, '..')
  : __dirname;

// Read version from root VERSION file or fallback
let appVersion = '0.1.0';
const versionPaths = [
  path.resolve(__dirname, '../VERSION'),
  path.resolve(__dirname, 'VERSION'),
];
for (const vp of versionPaths) {
  if (fs.existsSync(vp)) {
    try {
      const v = fs.readFileSync(vp, 'utf-8').trim();
      if (v) {
        appVersion = v;
        break;
      }
    } catch {
      // ignore
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  envDir: envDir,
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },

  server: {
    port: 8090,
    open: true,
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8089',
        changeOrigin: true,
        secure: false,
      }
    }
  },
  build: {
    outDir: 'dist',
  },
})
