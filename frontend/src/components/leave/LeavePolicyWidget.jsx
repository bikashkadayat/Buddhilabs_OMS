import React, { useEffect, useState } from 'react';
import { leaveService } from '../../services/leaveService';

// Dashboard "Leave Policy" widget. Shows the OFFICIAL NIF category-based leave
// entitlements, driven live from /leaves/leave-policy/ (the EntitlementRule
// matrix) — the same source as the balance cards, so it can never drift.
// The logged-in user's category is shown up top; a "View full policy" expander
// lists every category the server returns (A–E plus Probation).
const heading = {
  fontSize: 'var(--fs-sm)', fontWeight: 700, textTransform: 'uppercase',
  letterSpacing: '1.5px', marginBottom: '20px', color: 'var(--text-muted)',
};
const row = { display: 'flex', alignItems: 'flex-start', gap: '12px' };
const dot = (c) => ({ width: 8, height: 8, borderRadius: '50%', background: c, marginTop: 6, flexShrink: 0 });
const line = { fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.5 };
const DOT_COLORS = ['var(--brand-blue)', 'var(--brand-red)', 'var(--success, #10b981)', 'var(--draft, #6b7280)', 'var(--brand-blue)'];

const ItemList = ({ items }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
    {items.map((it, i) => (
      <div key={it.code} style={row}>
        <div style={dot(it.applicable ? DOT_COLORS[i % DOT_COLORS.length] : 'var(--text-muted)')} />
        <div style={line}>
          <strong style={{ color: 'var(--text-primary)' }}>{it.leave_type}:</strong> {it.value}
        </div>
      </div>
    ))}
  </div>
);

const LeavePolicyWidget = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let alive = true;
    leaveService.getLeavePolicy()
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, []);

  const mine = data?.categories?.find((c) => c.key === data.your_category);

  return (
    <div className="side-card" style={{ padding: '28px' }}>
      <h3 style={heading}>Leave Policy</h3>

      {!data && !error && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>Loading policy…</div>}
      {error && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>Could not load the leave policy right now.</div>}

      {data && (
        <>
          {mine ? (
            <>
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--text-primary)' }}>Category {mine.key}</div>
              <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', margin: '2px 0 18px' }}>{mine.label}</div>
              <ItemList items={mine.items} />
            </>
          ) : (
            // No resolved category — show the full policy directly.
            <ItemList items={data.categories[0]?.items || []} />
          )}

          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            aria-expanded={showAll}
            style={{
              marginTop: 20, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              background: 'none', border: 'none', padding: '8px 0', cursor: 'pointer',
              font: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--brand-blue)',
            }}
          >
            {showAll ? 'Hide full policy' : 'View full policy (all categories)'}
            <span style={{ display: 'inline-block', transform: showAll ? 'rotate(90deg)' : 'none', transition: 'transform .2s' }}>›</span>
          </button>

          {showAll && (
            <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 14, display: 'flex', flexDirection: 'column', gap: 18 }}>
              {data.categories.map((c) => (
                <div key={c.key}>
                  <div style={{
                    fontSize: 'var(--fs-meta)', fontWeight: 700, marginBottom: 8,
                    color: c.key === data.your_category ? 'var(--brand-blue)' : 'var(--text-primary)',
                  }}>
                    Category {c.key} · {c.label}{c.key === data.your_category ? ' — your category' : ''}
                  </div>
                  <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {c.items.map((it) => (
                      <li key={it.code} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 'var(--fs-meta)' }}>
                        <span style={{ color: 'var(--text-secondary)' }}>{it.leave_type}</span>
                        <span style={{ color: it.applicable ? 'var(--text-primary)' : 'var(--text-muted)', fontWeight: 500, textAlign: 'right' }}>{it.value}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default LeavePolicyWidget;
