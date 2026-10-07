import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Search, X } from 'lucide-react';

import { inventoryService } from '../../services/inventoryService';
import AssetInfoCard from './AssetInfoCard';

/**
 * Choosing an asset, when a dropdown of names is not enough
 * (Phase ASSET-TRANSFER-GOVERNANCE).
 *
 * WHY NOT A <select>
 * ------------------
 * The register holds hundreds of assets and many share a name: six "Dell Latitude
 * 5420" rows are indistinguishable in a dropdown, and the person raising a
 * transfer has to pick the right one. So each row carries what actually tells them
 * apart - the code, who is holding it, which department, and what condition it is
 * in - and the list is searchable by all four.
 *
 * THE SERVER DOES THE SEARCHING
 * -----------------------------
 * `?search=` is passed through rather than filtering a cached list in the browser.
 * Filtering here would only ever search the page that had been fetched, so an
 * asset absent from it would read as "no matches" rather than "not loaded" - and
 * the two look identical to the person typing.
 *
 * WHAT IT RETURNS
 * ---------------
 * The whole asset object, not an id. Everything the form then shows - owner,
 * department, category, condition, purchase date - is read off that one record, so
 * the form can never display a different asset's details from the one selected.
 */
const DEBOUNCE_MS = 250;

const AssetPicker = ({
  value, onChange, params = {}, label = 'Asset', required = true,
  placeholder = 'Search by name, code, owner or department',
  emptyHint = 'No asset matches that search.',
}) => {
  const [term, setTerm] = useState('');
  const [debounced, setDebounced] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(term.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [term]);

  // A click anywhere else closes the list. Without this the panel stays open
  // behind the rest of the form and swallows the next click.
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const query = useMemo(
    () => ({ ...params, ...(debounced ? { search: debounced } : {}) }),
    [params, debounced],
  );
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['inventory', 'items', 'picker', query],
    queryFn: () => inventoryService.items(query),
    enabled: open,
    staleTime: 30_000,
  });

  // A new search or a re-opened list starts the highlight at the top. Reset
  // during render when either changes, not in an effect that would first paint
  // the old highlight against the new rows.
  const [seen, setSeen] = useState({ debounced, open });
  if (seen.debounced !== debounced || seen.open !== open) {
    setSeen({ debounced, open });
    setActive(0);
  }

  const choose = (row) => {
    onChange(row);
    setOpen(false);
    setTerm('');
  };

  const onKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return; }
    if (!open) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, rows.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && rows[active]) { e.preventDefault(); choose(rows[active]); }
    else if (e.key === 'Escape') { setOpen(false); }
  };

  return (
    <div className="inv-picker" ref={boxRef}>
      {/*
        * The chosen state is NOT wrapped in <label>. A label labels whatever
        * control it contains, so the Change button inside one was announced as
        * "Asset * NIF-INV-0001 · Dell Latitude 5420" - the field's label read out
        * as the button's name. Only the search input, which is a real form
        * control, gets a label; the chosen row is a heading plus a button.
        */}
      {value ? (
        <div className="lr-field">
          <span className="inv-picker-label" id="inv-picker-label">
            {label}{required ? ' *' : ''}
          </span>
          <div className="inv-picker-chosen">
            <span>
              <strong>{value.asset_code}</strong> · {value.name}
            </span>
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => { onChange(null); setOpen(true); }}>
              <X size={13} aria-hidden="true" /> Change
            </button>
          </div>
        </div>
      ) : (
        <label className="lr-field">
          <span>{label}{required ? ' *' : ''}</span>
          <div className="inv-picker-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search" value={term} placeholder={placeholder}
              role="combobox" aria-expanded={open} aria-controls="inv-picker-list"
              aria-autocomplete="list"
              onChange={(e) => { setTerm(e.target.value); setOpen(true); }}
              onFocus={() => setOpen(true)}
              onKeyDown={onKeyDown}
            />
          </div>
        </label>
      )}

      {open && !value && (
        <ul className="inv-picker-list" id="inv-picker-list" role="listbox">
          {isLoading && <li className="inv-picker-note">Searching…</li>}
          {!isLoading && rows.length === 0 && (
            <li className="inv-picker-note">{emptyHint}</li>
          )}
          {rows.map((row, index) => (
            <li key={row.id} role="option" aria-selected={index === active}
              className={`inv-picker-row${index === active ? ' is-active' : ''}`}>
              <button type="button" onMouseEnter={() => setActive(index)}
                onClick={() => choose(row)}>
                <span className="inv-picker-title">
                  <strong>{row.name}</strong>
                  <code>{row.asset_code}</code>
                </span>
                <span className="inv-picker-meta">
                  {/* "Owner" is whoever holds it now. An asset in the store has
                      nobody accountable for it, and saying "In store" is a truer
                      answer than a blank that reads as missing data. */}
                  <span>{row.current_holder || 'In store'}</span>
                  <span>{row.department_name || 'No department'}</span>
                  <span>{row.condition_display || '—'}</span>
                  <span className={`min-status is-${row.status === 'assigned' ? 'ok' : 'muted'}`}>
                    {row.status_display}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Auto-populated from the chosen record itself, so it can never describe a
          different asset from the one above. Phase ASSET-VISIBILITY-AND-CUSTODY-
          DASHBOARD replaced a six-fact summary with the full ten-field card. */}
      {value && <AssetInfoCard asset={value} />}
      {value && !value.current_holder && (
        <div className="memo-notice is-warn" role="status">
          <Check size={14} aria-hidden="true" />
          Nobody is holding {value.asset_code}, so there is no custody to transfer.
          Assign it directly instead.
        </div>
      )}
    </div>
  );
};

export default AssetPicker;
