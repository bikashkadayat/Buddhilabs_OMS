import { createLogger, defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Kept in step with scripts/dev.mjs, which starts the API on the same port.
const API_PORT = Number(process.env.MORX_PORT || 8765)

/* Vite logs its own `http proxy error … ECONNREFUSED` stack for every proxied
 * request, which at one poll per 20s buries the terminal in a trace that says
 * nothing actionable. The proxy's own error handler below prints the useful
 * version instead, so this drops only that one message -- every other error
 * still comes through. */
const logger = createLogger()
const logError = logger.error.bind(logger)
logger.error = (message, options) => {
  if (typeof message === 'string' && message.includes('http proxy error')) return
  logError(message, options)
}

/* The build lands in morx/web/static/, which is exactly what the Python server
 * already serves -- so `python -m morx web` keeps working on a box with no Node
 * installed. The output is committed for that reason; only editing the UI needs
 * npm.
 *
 * `npm run dev` proxies /api to a running collector dashboard, so the UI can
 * hot-reload against real data.
 */
export default defineConfig({
  plugins: [react()],
  customLogger: logger,
  build: {
    outDir: '../morx/web/static',
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: 'assets',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        // Without this a stopped API prints a bare ECONNREFUSED stack trace
        // that says nothing about what to do, twice per poll.
        configure(proxy) {
          let warned = false
          proxy.on('error', (error, _req, res) => {
            if (error.code === 'ECONNREFUSED' && !warned) {
              warned = true
              console.log(
                `\n  The dashboard API is not running on :${API_PORT}.`
                + '\n  Start it with:  python -m morx web'
                + '\n  (or use `npm run dev`, which starts both.)\n',
              )
              // Re-arm, so it says this again if the API goes away later.
              setTimeout(() => { warned = false }, 10000)
            }
            if (res && typeof res.writeHead === 'function' && !res.headersSent) {
              res.writeHead(503, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: `dashboard API not running on :${API_PORT}` }))
            }
          })
        },
      },
    },
  },
})
