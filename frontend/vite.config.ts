import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react(), {
    name: 'demo-page-title',
    transformIndexHtml: (html) => mode === 'demo'
      ? html.replace('<title>Research Engine</title>', '<title>ResearchCouncil · Equity Research Demo</title>')
      : html,
  }],
  base: mode === 'demo' ? './' : '/',
  define: { __PUBLIC_DEMO__: JSON.stringify(mode === 'demo') },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    target: 'es2022',
    outDir: mode === 'demo' ? 'dist/demo' : 'dist',
    sourcemap: mode !== 'demo',
  },
}))
