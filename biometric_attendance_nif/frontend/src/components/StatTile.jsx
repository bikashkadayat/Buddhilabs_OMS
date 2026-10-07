export function StatTile({ label, value, sub }) {
  return (
    <div className="card stat-tile">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {sub ? <p className="stat-sub muted">{sub}</p> : null}
    </div>
  )
}

/** Exactly one per view: the number the dashboard leads with. */
export function HeroTile({ label, value, sub, children }) {
  return (
    <div className="card hero-card">
      <p className="stat-label">{label}</p>
      <p className="hero-figure">{value}</p>
      {sub ? <p className="stat-sub muted">{sub}</p> : null}
      {children}
    </div>
  )
}
