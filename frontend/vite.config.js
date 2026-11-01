import { defineConfig, loadEnv } from 'vite'
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

// index.html uses %VITE_LOGO_TITLE%; Vite leaves it literal when the variable is unset
// (desktop builds), so fill in the default here.
function logoTitle(mode) {
  const env = loadEnv(mode, envDir, 'VITE_');
  return {
    name: 'logo-title-default',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => html.replace(/%VITE_LOGO_TITLE%/g, env.VITE_LOGO_TITLE || process.env.VITE_LOGO_TITLE || 'AstroCat'),
    },
  };
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), logoTitle(mode)],
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
}))
