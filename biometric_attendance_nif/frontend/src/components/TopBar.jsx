import { useEffect, useState } from 'react'
import { fmtInt } from '../lib/format'

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem('morx-theme') || 'auto')

  useEffect(() => {
    if (theme === 'auto') {
      document.documentElement.removeAttribute('data-theme')
      localStorage.removeItem('morx-theme')
    } else {
      document.documentElement.setAttribute('data-theme', theme)
      localStorage.setItem('morx-theme', theme)
    }
  }, [theme])

  return [theme, setTheme]
}

const NEXT_THEME = { auto: 'light', light: 'dark', dark: 'auto' }
const THEME_LABEL = { auto: 'Auto', light: 'Light', dark: 'Dark' }

export function TopBar({ meta, error, loading }) {
  const [theme, setTheme] = useTheme()

  const status = error ? 'stale' : loading ? 'busy' : 'live'
  const statusText = error
    ? `Not reachable — ${error}`
    : meta
      ? `${fmtInt(meta.punches)} punches · ${fmtInt(meta.employees)} employees · ${meta.source.backend}`
      : 'Loading…'

  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>Attendance</h1>
        <p className="muted">{statusText}</p>
      </div>
      <div className="topbar-actions">
        <span className={`live ${status}`} title={error ? 'Cannot reach the collector' : 'Watching for new punches'} />
        <button
          type="button" className="ghost-button"
          onClick={() => setTheme(NEXT_THEME[theme])}
          title="Switch colour theme"
        >
          {THEME_LABEL[theme]}
        </button>
      </div>
    </header>
  )
}
