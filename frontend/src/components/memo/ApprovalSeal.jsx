import React from 'react';

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

/**
 * The approval seal banner for an approved or archived memo (Phase 26).
 *
 * The brief for the archive is that a reader must see APPROVED, the approval date
 * and the approver at first glance, without reading the activity log — so this
 * sits at the top of the detail page rather than beside the signature blocks
 * further down.
 *
 * Renders nothing when `certificate` is absent. The server returns it only for a
 * memo that really is approved or archived, so there is no status test here that
 * could drift from the workflow's own definition of "approved" and stamp an
 * in-flight memo.
 *
 * @param {{ certificate?: {stamp:string, approved_by:string, approved_at:string,
 *           designation?:string, department?:string, verification_id?:string,
 *           archived_at?:string} | null }} props
 */
const ApprovalSeal = ({ certificate }) => {
  if (!certificate) return null;

  return (
    <section className="memo-seal-banner" aria-label="Approval certificate">
      <div className="memo-seal">
        <div className="memo-seal-word">{certificate.stamp}</div>
      </div>
      <dl className="memo-seal-facts">
        <div>
          <dt>Approved by</dt>
          <dd>
            {certificate.approved_by}
            {certificate.designation && certificate.designation !== '—'
              && ` · ${certificate.designation}`}
          </dd>
        </div>
        <div><dt>Department</dt><dd>{certificate.department || '—'}</dd></div>
        <div><dt>Approved on</dt><dd>{fmt(certificate.approved_at)}</dd></div>
        {certificate.archived_at && (
          <div><dt>Archived on</dt><dd>{fmt(certificate.archived_at)}</dd></div>
        )}
        {certificate.verification_id && (
          <div>
            <dt>Verification</dt>
            <dd className="memo-seal-vid">{certificate.verification_id}</dd>
          </div>
        )}
      </dl>
    </section>
  );
};

export default ApprovalSeal;
