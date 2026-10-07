import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { History } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';

/**
 * The complete audit timeline for one asset (Phase 70.11).
 *
 * Every row here was written by the lifecycle engine at the moment a status
 * changed - the engine is the only thing that writes `item.status`, and it always
 * writes an event alongside. So this panel is a query rather than a
 * reconstruction, and an asset whose history looks wrong means the engine was
 * bypassed rather than that the display is lossy.
 *
 * Renders nothing at all for a reader the endpoint refuses, rather than
 * advertising a panel it cannot fill.
 */
const AssetHistory = ({ itemId }) => {
  const { data: rows, isError } = useQuery({
    queryKey: ['inventory', 'history', itemId],
    queryFn: () => assetLifecycle.history(itemId),
    enabled: Boolean(itemId),
    retry: false,
  });

  if (isError || !rows) return null;

  return (
    <div className="memo-panel" data-testid="asset-history">
      <h3 className="memo-panel-title">
        <History size={15} aria-hidden="true" /> Asset History
      </h3>
      {rows.length === 0 ? (
        <p className="lr-page-sub">Nothing has happened to this asset yet.</p>
      ) : (
        <ol className="inv-timeline">
          {rows.map((row) => (
            <li key={row.id} className="inv-timeline-row">
              <span className="inv-timeline-dot" aria-hidden="true" />
              <div className="inv-timeline-body">
                <div className="inv-timeline-head">
                  <b>{row.label}</b>
                  <span className="lr-page-sub">
                    {new Date(row.at).toLocaleString()}
                  </span>
                </div>
                <div className="lr-page-sub">
                  {row.actor}
                  {row.subject && row.subject !== row.actor && <> → {row.subject}</>}
                  {row.from_status !== row.to_status && (
                    <> · {row.from_status || '—'} → {row.to_status}</>
                  )}
                </div>
                {row.remarks && <div className="inv-timeline-note">{row.remarks}</div>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};

export default AssetHistory;
