import antfu from '@antfu/eslint-config'

// Backend (Bun + Hono) lint baseline. The sibling SPA (web/) carries its own
// lint story under pma-web; generated and data dirs are ignored.
export default antfu({
  type: 'app',
  typescript: true,
  ignores: [
    'web/**',
    'dist/**',
    'drizzle/**',
    '.data/**',
    'scripts/**', // python helpers
  ],
})
