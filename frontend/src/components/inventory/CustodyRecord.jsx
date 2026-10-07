import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FileText } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';

const CUSTODY = {
  held: { label: 'Held', tone: 'ok' },
  unassigned: { label: 'Unassigned', tone: 'muted' },
  transfer_in_progress: { label: 'Transfer in progress', tone: 'warn' },
  return_in_progress: { label: 'Return in progress', tone: 'warn' },
};

const Section = ({ title, empty, rows, children }) => (
  <section className="inv-custody-section">
    <h3 className="inv-custody-title">{title}</h3>
    {rows.length === 0
      ? <p className="lr-page-sub">{empty}</p>
      : <div className="resp-table-wrap"><table className="resp-table">{children}</table></div>}
  </section>
);

/**
 * The asset's custody record (Phase ASSET-CUSTODY-TRANSFER): who is accountable
 * for it now, whether it is on the move, and every transfer, repair and request
 * it has been through - plus its documents, served through signed links.
 *
 * Renders nothing when the endpoint refuses the reader, exactly as AssetHistory
 * does. An employee who is not the holder has no business seeing this, and an
 * empty panel would suggest there is simply nothing to see.
 */
const CustodyRecord = ({ itemId }) => {
  const { data, isError } = useQuery({
    queryKey: ['inventory', 'custody', itemId],
    queryFn: () => assetLifecycle.custody(itemId),
    retry: false,
  });
  if (isError || !data) return null;

  const custody = CUSTODY[data.custody_status] || CUSTODY.unassigned;
  const owner = data.current_owner;

  return (
    <div className="memo-panel inv-custody" data-testid="custody-record">
      <div className="memo-matrix-head">
        <h2 className="memo-panel-title" style={{ margin: 0 }}>Custody</h2>
        <span className={`min-status is-${custody.tone}`}>{custody.label}</span>
      </div>

      <dl className="memo-doc-meta">
        <div><dt>Current owner</dt>
          <dd>{owner ? owner.name : 'Nobody — in stock'}
            {owner && owner.is_active === false && (
              <span className="min-status is-no" style={{ marginLeft: 8 }}>Inactive account</span>
            )}</dd></div>
        <div><dt>Held since</dt><dd>{owner?.since || '—'}</dd></div>
        <div><dt>Current department</dt><dd>{data.current_department || '—'}</dd></div>
        {data.open_transfer && (
          <div><dt>Open transfer</dt>
            <dd>
              <Link to={`/inventory/transfers?open=${data.open_transfer.id}`}>
                {data.open_transfer.transfer_number}
              </Link>{' '}
              → {data.open_transfer.to_employee?.name} · {data.open_transfer.status_label}
            </dd></div>
        )}
      </dl>

      <Section title="Transfer History" empty="This asset has never been transferred."
        rows={data.transfers}>
        <thead><tr><th>Transfer</th><th>From</th><th>To</th><th>Reason</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
          {data.transfers.map((t) => (
            <tr key={t.id}>
              <td data-label="Transfer">
                <Link to={`/inventory/transfers?open=${t.id}`}>{t.transfer_number}</Link>
              </td>
              <td data-label="From">{t.from_employee?.name || '—'}</td>
              <td data-label="To">{t.to_employee?.name || '—'}</td>
              <td data-label="Reason">{t.reason_label}</td>
              <td data-label="Status">{t.status_label}</td>
              <td data-label="Date">{t.transfer_date}</td>
            </tr>
          ))}
        </tbody>
      </Section>

      <Section title="Maintenance History" empty="No faults have been reported."
        rows={data.maintenance_history}>
        <thead><tr><th>Ticket</th><th>Issue</th><th>Status</th><th>Reported</th></tr></thead>
        <tbody>
          {data.maintenance_history.map((m) => (
            <tr key={m.reference}>
              <td data-label="Ticket">{m.reference}</td>
              <td data-label="Issue">{m.issue}</td>
              <td data-label="Status">{m.status}</td>
              <td data-label="Reported">{new Date(m.reported_at).toLocaleDateString('en-GB')}</td>
            </tr>
          ))}
        </tbody>
      </Section>

      <Section title="Request History" empty="No requests have named this asset."
        rows={data.request_history}>
        <thead><tr><th>Request</th><th>Requested by</th><th>Status</th><th>Raised</th></tr></thead>
        <tbody>
          {data.request_history.map((r) => (
            <tr key={r.reference}>
              <td data-label="Request">{r.reference}</td>
              <td data-label="Requested by">{r.requested_by}</td>
              <td data-label="Status">{r.status}</td>
              <td data-label="Raised">{new Date(r.requested_at).toLocaleDateString('en-GB')}</td>
            </tr>
          ))}
        </tbody>
      </Section>

      <section className="inv-custody-section">
        <h3 className="inv-custody-title">Documents</h3>
        {data.documents.length === 0
          ? <p className="lr-page-sub">No documents are attached to this asset or its transfers.</p>
          : (
            <ul className="inv-transfer-files">
              {data.documents.map((doc) => (
                <li key={doc.url}>
                  <a href={doc.url} target="_blank" rel="noreferrer">
                    <FileText size={13} aria-hidden="true" /> {doc.name}
                  </a>
                  {doc.reference && <span className="lr-page-sub"> · {doc.reference}</span>}
                </li>
              ))}
            </ul>
          )}
      </section>
    </div>
  );
};

export default CustodyRecord;
