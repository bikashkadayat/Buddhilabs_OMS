import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { clamp } from '../lib/format'

const TooltipContext = createContext(null)

/** `const tip = useTooltip()` then `tip.show(event, title, rows)` / `tip.hide()`. */
export const useTooltip = () => useContext(TooltipContext)

export function TooltipProvider({ children }) {
  const [state, setState] = useState(null)

  const api = useMemo(() => ({
    show(event, title, rows) {
      setState({ x: event.clientX, y: event.clientY, title, rows })
    },
    hide() { setState(null) },
  }), [])

  return (
    <TooltipContext.Provider value={api}>
      {children}
      {state ? createPortal(<TooltipBox {...state} />, document.body) : null}
    </TooltipContext.Provider>
  )
}

function TooltipBox({ x, y, title, rows }) {
  const [size, setSize] = useState({ width: 160, height: 60 })

  // Measure on mount so the box can be flipped away from the viewport edges
  // instead of being clipped by them.
  const measure = useCallback((node) => {
    if (!node) return
    const rect = node.getBoundingClientRect()
    setSize({ width: rect.width, height: rect.height })
  }, [title, rows])

  const left = clamp(x + 14, 8, window.innerWidth - size.width - 8)
  const top = clamp(y - size.height - 12, 8, window.innerHeight - size.height - 8)

  return (
    <div className="tooltip" ref={measure} style={{ left, top }} role="status" aria-live="polite">
      {title ? <div className="tooltip-title">{title}</div> : null}
      {rows.map((row, i) => (
        <div className="tooltip-row" key={i}>
          {/* Line keys, not boxes: at tooltip density a filled swatch is
              data-weight ink doing a label's job. */}
          {row.color ? <span className="tooltip-key" style={{ background: row.color }} /> : null}
          <span className="tooltip-name">{row.name}</span>
          <span className="tooltip-value">{row.value}</span>
        </div>
      ))}
    </div>
  )
}
