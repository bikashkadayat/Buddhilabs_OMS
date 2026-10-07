import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Users } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';

/**
 * Who has held this asset, in order, in plain sentences
 * (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD).
 *
 *     Assigned to Bikash Kadayat
 *     Transferred to Raj Kumar      TRF-2026-0004
 *     Returned to Inventory
 *
 * The sentences are written by the server from the custody rows (see
 * backend/inventory/ownership.py), so this component only lays them out. It reads
 * the same `custody` query the Custody panel below it uses - one request for both.
 *
 * Renders nothing for a reader the endpoint refuses, as the other asset panels do.
 */
const KIND_TONES = {
  assigned: 'ok', transferred: 'ok', handed_over: 'ok',
  returned: 'muted', lost: 'no', disposed: 'no',
};

const OwnerHistory = ({ itemId }) => {
  const { data, isError } = useQuery({
    queryKey: ['inventory', 'custody', itemId],
    queryFn: () => assetLifecycle.custody(itemId),
    enabled: Boolean(itemId),
    retry: false,
  });
  if (isError || !data) return null;
  const entries = data.owner_timeline || [];

  return (
    <div className="memo-panel" data-testid="owner-history">
      <h3 className="memo-panel-title">
        <Users size={15} aria-hidden="true" /> Owner History
      </h3>
      {entries.length === 0 ? (
        <p className="lr-page-sub">This asset has never been assigned to anybody.</p>
      ) : (
        <ol className="inv-owner-history">
          {entries.map((entry, index) => (
            <li key={`${entry.kind}-${entry.at}-${index}`}
              className={`inv-owner-step is-${KIND_TONES[entry.kind] || 'muted'}${
                entry.is_current ? ' is-current' : ''}`}>
              <span className="inv-owner-dot" aria-hidden="true" />
              <div className="inv-owner-body">
                <strong>{entry.label}</strong>
                {entry.is_current && <span className="min-status is-ok">Current owner</span>}
                <div className="lr-page-sub">
                  {entry.date || '—'}
                  {entry.from_owner && entry.kind === 'transferred' && <> · from {entry.from_owner}</>}
                  {entry.by && <> · by {entry.by}</>}
                  {entry.reference && entry.kind === 'transferred' && (
                    <> · <Link to="/inventory/transfers">{entry.reference}</Link></>
                  )}
                  {entry.reference && entry.kind === 'disposed' && (
                    <> · <Link to="/inventory/disposals">{entry.reference}</Link></>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};

export default OwnerHistory;
