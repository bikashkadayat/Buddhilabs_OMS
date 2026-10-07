import React, { useMemo, useState } from 'react';
import { Search, Loader2, AlertCircle, PackageOpen, RefreshCw } from 'lucide-react';

/**
 * Phase 70.19-F/G — the take-out asset selector.
 *
 * Replaces a bare <select> whose only content was "— Select item —". That control
 * could not tell the four states apart: still loading, loaded and empty, failed to
 * load, and genuinely nothing eligible. All four rendered as an empty dropdown, so
 * a 403 from the server and "you hold no assets" looked identical to the user and
 * the bug went unreported as "the dropdown is just empty".
 *
 * Every state below is therefore explicit, and `emptyReason` comes from the SERVER
 * (the eligible-assets endpoint returns it) rather than being re-derived here: the
 * rule about who may take out what has one home, and the client should not own a
 * second copy of it in a string.
 */
const EligibleAssetSelect = ({
  assets,
  value,
  onChange,
  loading,
  error,
  emptyReason,
  onRetry,
  disabled,
}) => {
  const [query, setQuery] = useState('');

  // Filtered client-side: the list is one person's eligible assets, which is a
  // handful of rows, so a round trip per keystroke would be slower and noisier in
  // the audit log than filtering what we already hold. The server's `search`
  // parameter stays available for the day a store manager has thousands.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return assets;
    return assets.filter(a => (
      `${a.asset_code} ${a.name} ${a.category_name || ''} ${a.current_holder || ''}`
        .toLowerCase().includes(q)
    ));
  }, [assets, query]);

  if (loading) {
    return (
      <div className="asset-select-state" role="status" aria-live="polite">
        <Loader2 size={16} className="spin" />
        <span>Loading assets…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="asset-select-state asset-select-error" role="alert">
        <AlertCircle size={16} />
        <div>
          <strong>Error loading assets.</strong>
          <div className="asset-select-sub">{error}</div>
        </div>
        {onRetry && (
          <button type="button" className="asset-select-retry" onClick={onRetry}>
            <RefreshCw size={14} /> Try again
          </button>
        )}
      </div>
    );
  }

  if (!assets.length) {
    return (
      <div className="asset-select-state asset-select-empty">
        <PackageOpen size={16} />
        <div>
          <strong>No eligible assets found.</strong>
          <div className="asset-select-sub">
            {emptyReason
              || 'You currently have no take-out eligible assets. Contact your Inventory Officer.'}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="asset-select">
      {assets.length > 5 && (
        <div className="asset-select-search">
          <Search size={14} />
          <input
            type="text"
            value={query}
            placeholder="Search by code, name or category…"
            onChange={e => setQuery(e.target.value)}
            disabled={disabled}
            aria-label="Search eligible assets"
          />
        </div>
      )}

      {!filtered.length ? (
        <div className="asset-select-state asset-select-empty">
          <Search size={16} />
          <div>
            <strong>No assets match “{query}”.</strong>
            <div className="asset-select-sub">Clear the search to see all {assets.length}.</div>
          </div>
        </div>
      ) : (
        <ul className="asset-select-list" role="listbox" aria-label="Eligible assets">
          {filtered.map(a => {
            const selected = String(value) === String(a.id);
            return (
              <li key={a.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={disabled}
                  className={`asset-option${selected ? ' is-selected' : ''}`}
                  onClick={() => onChange(a.id)}
                >
                  <span className="asset-option-code">{a.asset_code}</span>
                  <span className="asset-option-name">{a.name}</span>
                  <span className="asset-option-meta">
                    {a.category_name || 'Uncategorised'}
                    {' · '}
                    {a.current_holder
                      ? `Assigned to ${a.is_mine ? 'you' : a.current_holder}`
                      : 'In stock'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default EligibleAssetSelect;
