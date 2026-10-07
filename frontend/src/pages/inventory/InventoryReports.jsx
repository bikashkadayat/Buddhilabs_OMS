import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, FileBarChart } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';
import { saveBlob } from '../../services/documentService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * Every inventory report (Phase 70.13, extended since).
 *
 * ONE page for all of them, with the columns derived from the rows the server
 * returns - exactly as the PDF template does. A hand-written table per report
 * would be one more place for a column to be renamed everywhere but one, and the
 * report that got missed would be the one nobody opens until an audit.
 *
 * Nested values (the per-employee asset list) are summarised rather than printed
 * into a cell: a table cell is the wrong shape for a list, and the count beside
 * the name already carries it.
 */
const REPORTS = [
  ['by_department', 'Assets by Department'],
  ['by_employee', 'Assets by Employee'],
  ['by_category', 'Assets by Category'],
  ['warranty_expiring', 'Warranty Expiring'],
  ['in_maintenance', 'Assets in Maintenance'],
  ['disposed', 'Disposed Assets'],
  ['unreturned', 'Unreturned Assets'],
  // Phase ASSET-CUSTODY-TRANSFER. "Department" and "Employee" asset reports are
  // the by_department and by_employee entries above.
  ['ownership', 'Asset Ownership'],
  ['movement', 'Asset Movement'],
  ['transfers', 'Transfer History'],
  ['exit_clearance', 'Exit Clearance Assets'],
  // Phase ASSET-LIFECYCLE-DISPOSAL. "Warranty Expiry" and "Disposed Assets" are
  // the warranty_expiring and disposed entries above.
  ['amc_expiring', 'AMC Expiring'],
  ['depreciation', 'Depreciation Register'],
  ['lifecycle', 'Asset Lifecycle'],
  ['write_off', 'Write-off Register'],
  ['lost', 'Lost Assets'],
  // Phase ASSET-TRANSFER-GOVERNANCE.
  ['department_assets', 'Department-wise Assets'],
  ['organisation_assets', 'Organization Assets'],
  ['transfer_visibility', 'Transfer Visibility'],
];

const humanise = (key) =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const cell = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return `${value.length} item(s)`;
  if (typeof value === 'object') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
};

const InventoryReports = () => {
  const { name = 'by_department' } = useParams();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'report', name],
    queryFn: () => assetLifecycle.report(name),
  });

  const download = async () => {
    saveBlob(await assetLifecycle.reportPdf(name), `inventory-${name}.pdf`);
  };

  const rows = data?.rows || [];
  const columns = [];
  rows.forEach((row) => Object.keys(row).forEach((key) => {
    if (!columns.includes(key) && !Array.isArray(row[key])
        && typeof row[key] !== 'object') columns.push(key);
  }));

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Inventory Reports</h1>
          <p className="lr-page-sub">
            Figures are live and change as assets move
          </p>
        </div>
        <button type="button" className="lr-btn" onClick={download}>
          <Download size={14} /> Download PDF
        </button>
      </div>

      <div className="memo-filters" role="tablist" aria-label="Reports">
        {REPORTS.map(([key, label]) => (
          <Link key={key} to={`/inventory/reports/${key}`}
            className={`lr-btn ${key === name ? 'lr-btn-primary' : ''}`}>
            {label}
          </Link>
        ))}
      </div>

      {isLoading && <Skeleton rows={5} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">
            <FileBarChart size={15} aria-hidden="true" /> {data.label}
          </h3>
          {rows.length === 0 ? (
            <p className="lr-page-sub">
              No rows. That is an answer rather than a failure — nothing currently
              matches this report.
            </p>
          ) : (
            <div className="lr-table-wrap">
              <table className="lr-table inv-register">
                <thead>
                  <tr>{columns.map((c) => <th key={c}>{humanise(c)}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    // Position is part of the key: a reference is not unique in
                    // an event log (one transfer writes an ownership change AND a
                    // department change), and keying on it alone made React drop
                    // or duplicate rows in the Asset Movement report. These rows
                    // are never re-sorted client-side, so position is stable.
                    <tr key={`${row.id || row.reference || row.code || 'row'}-${index}`}>
                      {columns.map((c, position) => (
                        <td key={c} className={position === 0 ? 'is-primary' : ''}>
                          {cell(row[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default InventoryReports;
