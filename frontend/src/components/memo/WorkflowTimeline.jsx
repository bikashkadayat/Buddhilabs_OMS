import React from 'react';
import {
  Archive, CheckCircle, Clock, CornerUpLeft, Eye, FileText, MessageSquare,
  Send, Slash, ThumbsUp, XCircle,
} from 'lucide-react';

// Icon + colour per timeline event. Completed events use their own colour;
// outstanding steps are drawn muted so the eye lands on what has happened.
const EVENT = {
  created: { label: 'Created', color: '#6B7280', Icon: FileText },
  submitted: { label: 'Submitted', color: '#2563EB', Icon: Send },
  sent_for_review: { label: 'Sent for Review', color: '#2563EB', Icon: Send },
  reviewed: { label: 'Reviewed', color: '#4F46E5', Icon: Eye },
  recommended: { label: 'Recommended', color: '#0891B2', Icon: ThumbsUp },
  supported: { label: 'Supported', color: '#0D9488', Icon: ThumbsUp },
  approved: { label: 'Approved', color: '#059669', Icon: CheckCircle },
  rejected: { label: 'Rejected', color: '#DC2626', Icon: XCircle },
  returned: { label: 'Returned', color: '#6366F1', Icon: CornerUpLeft },
  archived: { label: 'Archived', color: '#475569', Icon: Archive },
  cancelled: { label: 'Cancelled', color: '#6B7280', Icon: Slash },
  commented: { label: 'Commented', color: '#6B7280', Icon: MessageSquare },
  // Upcoming steps are keyed by role type rather than by a past-tense action.
  reviewer: { label: 'Review', color: '#94A3B8', Icon: Clock },
  recommender: { label: 'Recommendation', color: '#94A3B8', Icon: Clock },
  supporter: { label: 'Support', color: '#94A3B8', Icon: Clock },
  approver: { label: 'Approval', color: '#94A3B8', Icon: Clock },
};

const clockTime = (iso) => new Date(iso).toLocaleTimeString(undefined, {
  hour: '2-digit', minute: '2-digit',
});
const fullStamp = (iso) => new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
});

const initials = (name) => (name || '?').split(' ').map((w) => w[0] || '')
  .join('').slice(0, 2).toUpperCase();

/**
 * Phase 7 activity timeline: what has happened, followed by what is still
 * outstanding and who it is waiting on.
 *
 * The previous timeline could only render past actions, because nothing existed
 * in the database until someone acted. Upcoming entries come from the workflow
 * matrix, so the reader can see the whole route rather than just its history.
 *
 * @param {{ entries:Array<Object> }} props  from GET /memos/{id}/timeline/
 */
const WorkflowTimeline = ({ entries = [] }) => {
  if (!entries.length) {
    return <p className="lr-page-sub">No workflow activity yet.</p>;
  }

  return (
    <ol className="memo-timeline" aria-label="Memo activity timeline">
      {entries.map((entry) => {
        const meta = EVENT[entry.action] || EVENT.commented;
        const Icon = meta.Icon;
        const upcoming = entry.kind === 'upcoming';
        const color = upcoming && entry.state === 'active' ? '#D97706' : meta.color;

        return (
          <li
            key={`${entry.kind}-${entry.id}`}
            className={`memo-tl-item ${upcoming ? 'is-upcoming' : ''} ${entry.state === 'active' ? 'is-active' : ''}`}
            style={{ '--tl-color': color }}
          >
            <span className="memo-tl-time">
              {entry.at ? clockTime(entry.at) : '—'}
            </span>
            <span className="memo-tl-dot" style={{ background: color }}>
              <Icon size={12} color="#fff" aria-hidden="true" />
            </span>
            <div className="memo-tl-body">
              <div className="memo-tl-head">
                <span className="memo-tl-avatar" aria-hidden="true">{initials(entry.actor_name)}</span>
                <b>{entry.actor_name}</b>
                <span className="memo-tl-label" style={{ color }}>{entry.label}</span>
                {entry.at && (
                  <time className="memo-tl-stamp" dateTime={entry.at} title={fullStamp(entry.at)}>
                    {fullStamp(entry.at)}
                  </time>
                )}
              </div>
              {entry.designation && <div className="memo-tl-sub">{entry.designation}</div>}
              {entry.remarks && <div className="memo-tl-remarks">“{entry.remarks}”</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
};

export default WorkflowTimeline;
