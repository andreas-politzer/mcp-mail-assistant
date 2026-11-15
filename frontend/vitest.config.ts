import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The tests talk to a real admin API (started by tests/global-setup.ts) and use a stand-in for the
// Rust side. Run them with: npm test
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    globalSetup: ['tests/global-setup.ts'],
    setupFiles: ['tests/support/setup.ts'],
    env: { VITE_ADMIN_BASE: 'http://127.0.0.1:18431' },
    testTimeout: 60000,
    hookTimeout: 60000,
    fileParallelism: false,
  },
})
