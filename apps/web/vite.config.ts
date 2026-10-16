import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { instanceDefine, localApiTarget } from './instance-config.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: instanceDefine(),
  server: { proxy: { '/api': localApiTarget(), '/mcp': localApiTarget() } },
})
