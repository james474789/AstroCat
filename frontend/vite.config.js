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

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  envDir: envDir,
  server: {
    port: 8090,
    open: true,
  },
  build: {
    outDir: 'dist',
  },
})
