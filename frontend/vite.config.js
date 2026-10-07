import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // loadEnv, not bare process.env: Vite injects .env files into the *client*
  // bundle, but it does not put them in process.env for this config file. So a
  // VITE_API_URL set in frontend/.env.local was read by application code and
  // silently ignored by the proxy below — which is the one place it actually
  // needs to take effect. Reading it explicitly makes `npm run dev` on its own
  // behave the same as `run-local.sh`, which exports the variable instead.
  const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env }
  const apiUrl = env.VITE_API_URL || 'http://localhost:8000'

  return {
  plugins: [react()],
  server: {
    port: 5173,
    // Bind to 0.0.0.0 so a phone on the same Wi-Fi can reach the dev server by LAN
    // IP. Only the Vite port needs exposing: /api/v1, /media and /ws are proxied to
    // the backend server-side (target `apiUrl`), so `localhost:8001` stays correct
    // there and the phone never talks to the backend directly.
    host: true,
    proxy: {
      '/api/v1': {
        target: apiUrl,
        changeOrigin: true,
        secure: false,
      },
      // Uploaded media (profile photos, memo attachments) live on the backend.
      // Without this, /media/* falls through to the SPA index.html and every
      // <img src="/media/..."> renders broken.
      '/media': {
        target: apiUrl,
        changeOrigin: true,
        secure: false,
      },
      // Live attendance stream. useAttendanceStream dials
      // `${window.location.host}/ws/attendance/`, which under `npm run dev` is
      // the Vite server, not Django — so without this entry the handshake hits
      // the SPA, fails, and the hook falls back to polling. The failure is
      // invisible: punches still appear, just up to 20s late, so it reads as
      // lag rather than as a proxy that was never configured.
      //
      // `ws: true` is what makes Vite forward the Upgrade handshake instead of
      // answering it as a normal HTTP request.
      '/ws': {
        target: apiUrl,
        ws: true,
        changeOrigin: true,
        secure: false,
      },
    },
  },
  }
})
