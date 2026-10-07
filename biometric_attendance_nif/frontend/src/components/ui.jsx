/* Small, unopinionated building blocks. Nothing here knows about attendance. */

import { useEffect, useRef, useState } from 'react'

export function Card({ children, className = '', ...rest }) {
  return <section className={`card ${className}`} {...rest}>{children}</section>
}

export function CardHead({ title, subtitle, children }) {
  return (
    <div className="card-head">
      <div className="card-titles">
        <h2>{title}</h2>
        {subtitle ? <p className="muted">{subtitle}</p> : null}
      </div>
      {children ? <div className="head-actions">{children}</div> : null}
    </div>
  )
}

/** A chart card with a chart/table switch, so every value is reachable without
 *  hovering -- the accessibility fallback, not a power-user feature. */
export function ChartCard({ title, subtitle, table, actions, children, className = '' }) {
  const [showTable, setShowTable] = useState(false)
  return (
    <Card className={`chart-card ${className}`}>
      <CardHead title={title} subtitle={subtitle}>
        {actions}
        {table ? (
          <button
            type="button" className="ghost-button"
            aria-pressed={showTable}
            onClick={() => setShowTable((v) => !v)}
          >
            {showTable ? 'Chart' : 'Table'}
          </button>
        ) : null}
      </CardHead>
      {showTable && table ? <DataTable {...table()} scroll /> : children}
    </Card>
  )
}

export function DataTable({ headers, rows, scroll = false }) {
  return (
    <div className={scroll ? 'table-scroll capped' : 'table-scroll'}>
      <table>
        <thead>
          <tr>{headers.map((h, i) => <th key={h} className={i === 0 ? 'left' : undefined}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => <td key={j} className={j === 0 ? 'left' : undefined}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Dropdown that closes on outside click and on Escape. */
export function Popover({ label, children, align = 'right', className = '' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onPointerDown = (event) => {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false)
    }
    const onKey = (event) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className={`popover ${className}`} ref={ref}>
      <button
        type="button" className="ghost-button" aria-expanded={open} aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        {label} <span className="caret">▾</span>
      </button>
      {open ? (
        <div className={`popover-body ${align}`} role="menu">
          {typeof children === 'function' ? children(() => setOpen(false)) : children}
        </div>
      ) : null}
    </div>
  )
}

/** Mutually exclusive choices, shown at once. Cheaper to read than a select
 *  when there are only a few and one is nearly always what you want. */
export function SegmentedControl({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="segment"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <em className="field-hint">{hint}</em> : null}
    </label>
  )
}
