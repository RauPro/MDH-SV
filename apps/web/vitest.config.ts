import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { instanceDefine } from './instance-config.ts';
import { fileURLToPath } from 'node:url';
const baseline = fileURLToPath(new URL('../../tests/fixtures/upstream-instance.json', import.meta.url));
export default defineConfig({ plugins: [react()], define: instanceDefine({ MDH_INSTANCE_FILE: baseline }), test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], include: ['src/**/*.test.{ts,tsx}'] } });
