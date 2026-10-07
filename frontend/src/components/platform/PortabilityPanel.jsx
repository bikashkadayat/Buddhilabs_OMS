import React, { useCallback, useEffect, useState } from 'react';
import DataTable from '../common/DataTable';
import StatusBadge from '../common/StatusBadge';
import { platformService, bytes } from '../../services/platformService';

/**
 * Phase S6.5: export a customer's data, close their workspace, reopen it.
 *
 * THE THREE ACTIONS ARE DELIBERATELY NOT EQUALLY EASY. Exporting is a button;
 * archiving asks for a typed reason and says what it will and will not do;
 * restoring is a button again, because it is the undo. The asymmetry matches
 * the consequences — an export is additive, an archive locks every one of a
 * customer's users out of their own HR system.
 *
 * WHAT THE OPERATOR IS TOLD BEFORE ARCHIVING is the part worth getting right.
 * "Archive" sounds like "delete" to most people and means nearly the opposite
 * here, so the dialog states both halves: nobody can sign in, and nothing is
 * removed. Somebody who wanted the data gone will otherwise believe they have
 * achieved it.
 *
 * A FAILED EXPORT IS SHOWN, NOT HIDDEN. The server answers 201 with a receipt
 * that says `failed` when the integrity pass refused the bundle, because the
 * request was handled and that outcome is the answer. Rendering it as an
 * error would suggest the console broke; rendering it as a row with its
 * reason is what lets an operator act on it.
 */
const CONTENTS = [
  ['full', 'Everything (JSON, CSV and files)'],
  ['json', 'JSON only (restorable)'],
  ['csv', 'CSV only (readable)'],
];

const PortabilityPanel = ({ slug, organization, health, onChanged }) => {
  const [rows, setRows] = useState([]);
  const [contents, setContents] = useState('full');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState('');

  const load = useCallback(() => {
    platformService.exports(slug)
      .then((response) => setRows(response.data))
      .catch(() => setError('The export history could not be loaded.'));
  }, [slug]);

  useEffect(load, [load]);

  const archived = organization?.status === 'archived';
  const archiveState = health?.archive_state || {};

  const runExport = async () => {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const { data } = await platformService.createExport(slug, contents);
      setNotice(data.status === 'ready'
        ? `Export ready: ${data.row_count} rows, ${data.media_count} file(s).`
        : `Export ${data.status}: ${data.error || 'see the receipt below.'}`);
      load();
      onChanged?.();
    } catch {
      setError('The export could not be started.');
    } finally {
      setBusy(false);
    }
  };

  const download = async (row) => {
    setError(null);
    try {
      const checksum = await platformService.downloadExport(
        slug, row.id, `${slug}-export.zip`);
      setNotice(checksum
        ? `Downloaded. Verify the file against SHA-256 ${checksum}.`
        : 'Downloaded.');
      load();
    } catch {
      setError('The bundle could not be downloaded.');
    }
  };

  const archive = async () => {
    setBusy(true);
    setError(null);
    try {
      await platformService.archive(slug, reason);
      setConfirming(false);
      setReason('');
      setNotice('Workspace archived. Nothing was deleted.');
      onChanged?.();
    } catch (err) {
      setError(err.response?.data?.detail
        || 'The workspace could not be archived.');
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    setBusy(true);
    setError(null);
    try {
      await platformService.restore(slug, {});
      setNotice('Workspace restored to the state it was archived in.');
      onChanged?.();
    } catch (err) {
      setError(err.response?.data?.detail
        || 'The workspace could not be restored.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="pf-panel" aria-label="Data portability and archive">
      <h2 className="pf-panel-title">Data and archive</h2>

      {notice && <p className="pf-ok" role="status">{notice}</p>}
      {error && <p className="pf-err" role="alert">{error}</p>}

      {archived ? (
        <div className="pf-alert" role="status">
          <strong>This workspace is archived.</strong>
          <p>
            Nobody can sign in. Nothing has been deleted — every record, file
            and audit entry is preserved, and a restore returns the customer to{' '}
            <b>{archiveState.status_before_archive || 'where they were'}</b>.
          </p>
          {archiveState.archived_reason && (
            <p className="pf-muted">Reason: {archiveState.archived_reason}</p>
          )}
          <button type="button" className="btn btn-primary" onClick={restore}
                  disabled={busy}>
            {busy ? 'Restoring…' : 'Restore workspace'}
          </button>
        </div>
      ) : (
        <p className="pf-note">
          Exporting is always available, including after archiving — handing a
          departed customer their data is usually the reason to archive rather
          than cancel.
        </p>
      )}

      <div className="pf-actions">
        <label className="pf-field">
          <span>Export contents</span>
          <select value={contents} onChange={(e) => setContents(e.target.value)}>
            {CONTENTS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-primary" onClick={runExport}
                disabled={busy}>
          {busy ? 'Exporting…' : 'Export data'}
        </button>
        {!archived && (
          <button type="button" className="pf-btn-danger"
                  onClick={() => setConfirming(true)} disabled={busy}>
            Archive workspace
          </button>
        )}
      </div>

      <DataTable
        columns={[
          { key: 'created_at', header: 'When',
            render: (row) => new Date(row.created_at).toLocaleString() },
          { key: 'contents', header: 'Contents' },
          { key: 'status', header: 'Status',
            render: (row) => <StatusBadge status={row.status} /> },
          { key: 'row_count', header: 'Rows', align: 'right' },
          { key: 'media_count', header: 'Files', align: 'right' },
          { key: 'size_bytes', header: 'Size', align: 'right',
            render: (row) => bytes(row.size_bytes) },
          { key: 'requested_by_email', header: 'By',
            render: (row) => row.requested_by_email || 'system' },
          { key: 'download_count', header: 'Downloads', align: 'right' },
          { key: 'actions', header: '',
            render: (row) => (row.is_downloadable ? (
              <button type="button" className="btn btn-ghost"
                      onClick={() => download(row)}>
                Download
              </button>
            ) : (
              <span className="pf-muted" title={row.error || ''}>
                {row.status === 'expired' ? 'discarded' : (row.error ? 'failed' : '—')}
              </span>
            )) },
        ]}
        rows={rows}
        caption="Exports of this organization"
        empty={{ variant: 'first', title: 'No exports yet',
                 body: 'An export is a complete, checksummed copy of this '
                       + 'customer’s workspace.' }}
      />

      {confirming && (
        <div className="pf-modal" role="dialog" aria-modal="true"
             aria-labelledby="archive-title">
          <div className="pf-modal-card">
            <h3 className="pf-modal-title" id="archive-title">
              Archive {organization?.name}?
            </h3>
            <p className="pf-modal-lead">
              Every user of this organization will be locked out immediately.
            </p>
            <p className="pf-modal-note">
              <b>Nothing is deleted.</b> Records, documents, audit history and
              the subscription are all preserved, and a restore returns the
              workspace to <b>{organization?.status}</b>. Exporting still works
              while archived.
            </p>
            <label className="pf-field">
              <span>Reason (recorded, and shown to whoever asks later)</span>
              <input value={reason} onChange={(e) => setReason(e.target.value)}
                     placeholder="Customer closed their account" />
            </label>
            <div className="pf-modal-actions">
              <button type="button" className="btn btn-ghost"
                      onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button type="button" className="pf-btn-danger" onClick={archive}
                      disabled={busy || !reason.trim()}>
                {busy ? 'Archiving…' : 'Archive workspace'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

export default PortabilityPanel;
