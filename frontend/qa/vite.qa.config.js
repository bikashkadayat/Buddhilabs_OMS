/**
 * Separate Vite build for the visual QA harness (Phase 46 item 1).
 *
 * Its own config so the harness never enters the production bundle, and so the app's own
 * build output is untouched. `base: './'` makes the built page work from a file:// URL,
 * which is what lets headless Chrome screenshot it without a server.
 */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  root: here,
  base: './',
  resolve: {
    // The pages call useAuth(), which is useContext(AuthContext) with no default value.
    // Outside a provider it returns undefined and destructuring it throws, which renders
    // a blank page that the overflow probe then declares clean. Aliasing the module is
    // preferable to wrapping the harness in the real AuthProvider, because that provider
    // calls the /me endpoint on mount and there is no server here.
    // The pattern must match the WHOLE specifier: a regex alias substitutes only the
    // matched span, so /hooks\/useAuth$/ would rewrite "../../hooks/useAuth" to
    // "../.." + the absolute path and fail to resolve.
    // A regex alias matches the SPECIFIER AS WRITTEN, not the resolved file, so one
    // pattern per import form is required. Both bare forms below are unambiguous in
    // this codebase: `./api` is imported only by the 20 modules in src/services/, and
    // `./useAuth` only by src/hooks/useLeaves.js. Missing them is not cosmetic — it
    // is what made the first run of the Phase 100.1 leave screens die with
    // "Cannot destructure property 'user' of undefined" while the stub sat unused.
    alias: [
      // The `(\.jsx)?` is not decoration. Most call sites write
      // `../../hooks/useAuth`, but a few write `../../hooks/useAuth.jsx`, and an
      // alias regex matches the SPECIFIER AS WRITTEN. Without the optional
      // extension the extension-carrying imports slipped past the stub and loaded
      // the real hook, whose context has no provider here — so `const { user } =
      // useAuth()` threw and every screen rendering that component (the two
      // REAL_RAIL screens and all four mobile ones) died inside the boundary.
      { find: /^.*hooks\/useAuth(\.jsx)?$/, replacement: resolve(here, 'auth-stub.jsx') },
      { find: /^\.\/useAuth(\.jsx)?$/, replacement: resolve(here, 'auth-stub.jsx') },
      { find: /^\.\/api$/, replacement: resolve(here, 'api-stub.js') },
      // Phase 100.1. The Leave pages are useState + useEffect calling leaveService
      // directly, so there is no react-query cache to seed and they rendered their
      // error state — which is why Leave, Notifications and Reports were absent from
      // the Phase 100 audit. Every service imports this ONE axios instance, so
      // aliasing it here unlocks all of them at once. Same whole-specifier rule as
      // above: the pattern has to swallow the leading `../../`.
      { find: /^.*services\/api$/, replacement: resolve(here, 'api-stub.js') },
    ],
  },
  build: {
    outDir: resolve(here, '../dist-qa'),
    emptyOutDir: true,
    rollupOptions: {
      input: [resolve(here, 'visual-qa.html'), resolve(here, 'frame.html')],
    },
  },
});
