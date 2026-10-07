import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // Both are build output. `dist-qa` (npm run qa:build) is gitignored, so CI's
  // fresh checkout never sees it — but locally it makes `npm run lint` report
  // ~280 errors from minified bundles, which reads as a broken lint gate.
  globalIgnores(['dist', 'dist-qa']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: { ...globals.browser, process: true },
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // Capitalised names are exempt because ESLint's core scope analysis does
      // not count a JSX tag as a use: `<Icon />` leaves `Icon` looking unused.
      // Imported components are variables and were already covered; a component
      // received as a prop - `({ icon: Icon }) => <Icon />` - is a PARAMETER,
      // which varsIgnorePattern never reached, so every such component reported
      // a false "unused" warning. Lowercase unused parameters are still caught.
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^[A-Z_]' }],
      // These were downgraded to warnings while pre-existing debt was cleared
      // (PHASE CI-LINT-BUILD-FIX cleared it). Back to errors, so a new finding
      // fails CI instead of joining a pile of warnings nobody reads.
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/exhaustive-deps': 'error',
      'react-hooks/refs': 'error',
    },
  },
])
