import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, ShieldCheck } from 'lucide-react';
import { memoService } from '../../services/memoService';
import Skeleton from '../common/Skeleton';

const stamp = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
}) : '—');

/**
 * The full audit trail for a memo: actor, action, timestamp, client IP and
 * remarks for every recorded event.
 *
 * Renders nothing at all unless the request succeeds. The endpoint is restricted
 * to HR and administrators, so for everyone else this returns 403 and the panel
 * simply is not there — better than showing a locked section that advertises data
 * the viewer cannot have. Collapsed by default and fetched only when opened, so
 * the ordinary case of reading a memo costs no extra request.
 *
 * @param {{ memoId:string }} props
 */
/**
 * @param {{ memoId?:string, queryKey?:Array, queryFn?:Function }} props
 *   Pass `memoId` for a memo. Pass `queryKey` + `queryFn` for any other document type
 *   - the minute module uses the second form, so this panel is shared rather than
 *   copied. The trail's shape (action label, actor, IP, remarks, timestamp) is
 *   identical across modules, which is what makes one component correct here.
 */
const AuditTrailPanel = ({ memoId, queryKey, queryFn }) => {
  const [open, setOpen] = useState(false);

  const { data: entries, isLoading, isError } = useQuery({
    queryKey: queryKey || ['memo', memoId, 'audit-trail'],
    queryFn: queryFn || (() => memoService.getAuditTrail(memoId)),
    enabled: open,
    retry: false,
  });

  // A 403 is the expected answer for most users, not an error worth surfacing.
  if (open && isError) return null;

  return (
    <div className="memo-panel">
      <button type="button" className="memo-audit-toggle" aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}>
        <span className="memo-panel-title" style={{ margin: 0 }}>
          <ShieldCheck size={15} aria-hidden="true" /> Audit Trail
        </span>
        {open ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
      </button>

      {open && (
        <>
          <p className="lr-page-sub" style={{ marginTop: 8 }}>
            Every recorded event, with the address it came from. Restricted to HR and
            administrators.
          </p>
          {isLoading && <Skeleton rows={2} label="Loading audit trail" />}
          {!isLoading && entries?.length === 0 && (
            <p className="lr-page-sub">No audit entries recorded.</p>
          )}
          {!isLoading && entries?.length > 0 && (
            <div className="lr-table-wrap">
              <table className="lr-table memo-audit-table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">User</th>
                    <th scope="col">Action</th>
                    <th scope="col">IP Address</th>
                    <th scope="col">Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr key={entry.id}>
                      <td style={{ whiteSpace: 'nowrap' }}>{stamp(entry.at)}</td>
                      <td>{entry.actor}</td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{entry.action}</div>
                        {entry.transition && (
                          <div className="lr-page-sub">{entry.transition.replace(/_/g, ' ')}</div>
                        )}
                      </td>
                      <td className="memo-mono-cell">{entry.ip_address || '—'}</td>
                      <td className="memo-remarks-cell">{entry.remarks || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AuditTrailPanel;
