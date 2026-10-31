import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { instanceDefine, localApiTarget } from './instance-config.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: instanceDefine(),
  // Small font subsets would inline as data: URLs, which font-src 'self' blocks.
  build: { assetsInlineLimit: 0 },
  server: { proxy: { '/api': localApiTarget(), '/mcp': localApiTarget() } },
})
