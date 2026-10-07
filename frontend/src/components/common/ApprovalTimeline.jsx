import React from 'react';
import { CheckCircle, XCircle, Clock, Send } from 'lucide-react';

// Reusable approval timeline: Employee -> Department Head -> HR.
// Expects steps: [{ stage, name, role, date_ad, date_bs, status, remarks }]
const ICONS = {
  approved: { Icon: CheckCircle, cls: 'ar-tl-approved' },
  submitted: { Icon: Send, cls: 'ar-tl-submitted' },
  rejected: { Icon: XCircle, cls: 'ar-tl-rejected' },
  pending: { Icon: Clock, cls: 'ar-tl-pending' },
};

const fmtDate = (ad, bs) => {
  if (!ad) return null;
  const adStr = new Date(ad).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  return bs ? `${bs} BS · ${adStr}` : adStr;
};

const ApprovalTimeline = ({ steps = [] }) => {
  if (!steps.length) return null;
  return (
    <ol className="ar-timeline" aria-label="Approval timeline">
      {steps.map((s, i) => {
        const { Icon, cls } = ICONS[s.status] || ICONS.pending;
        const when = fmtDate(s.date_ad, s.date_bs);
        return (
          <li key={i} className={`ar-tl-item ${cls}`}>
            <span className="ar-tl-node" aria-hidden="true"><Icon size={16} /></span>
            <div className="ar-tl-body">
              <div className="ar-tl-head">
                <span className="ar-tl-stage">{s.stage}</span>
                <span className={`ar-tl-status ${cls}`}>{s.status}</span>
              </div>
              <div className="ar-tl-meta">
                {s.name ? <span className="ar-tl-name">{s.name}</span> : <span className="ar-tl-name muted">Awaiting action</span>}
                {when && <span className="ar-tl-date">{when}</span>}
              </div>
              {s.remarks ? <div className="ar-tl-remarks">“{s.remarks}”</div> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
};

export default ApprovalTimeline;
