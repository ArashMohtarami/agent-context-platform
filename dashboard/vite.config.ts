import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/console/',
  plugins: [react()],
  envDir: false,
  build: { assetsInlineLimit: 0, sourcemap: false, target: 'es2022' },
  test: {
    environment: 'jsdom', setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    restoreMocks: true, clearMocks: true, maxWorkers: 2,
  },
});
