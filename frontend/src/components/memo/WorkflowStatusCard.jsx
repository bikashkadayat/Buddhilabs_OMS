import React from 'react';
import { Clock, UserCheck, ArrowRight, CheckCircle2 } from 'lucide-react';
import { roleTypeLabel } from './memoLabels';

/**
 * Where the memo is right now (Phase MEMO-WORKFLOW-FINAL-ENTERPRISE).
 *
 * The page already had the tracker (which stage), the matrix table (who, when,
 * what they said) and the timeline (what has happened). What none of them
 * answered at a glance was the question people actually open a memo to ask:
 * *who is it sitting with, and since when?* The tracker shows an active pill
 * but not the person; the table shows the person but you have to find the row.
 *
 * Reads the same `workflow_steps` the table does — no new endpoint, and
 * therefore nothing that can disagree with the table beneath it.
 */

/** "3 days" / "4 hours" / "just now" — how long the current actor has held it. */
const since = (iso) => {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'}`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'}`;
};

const Row = ({ icon: Icon, label, children }) => (
  <div className="wsc-row">
    <span className="wsc-ico"><Icon size={15} strokeWidth={2.25} aria-hidden="true" /></span>
    <span className="wsc-label">{label}</span>
    <span className="wsc-value">{children}</span>
  </div>
);

const WorkflowStatusCard = ({ steps = [], status }) => {
  if (!steps.length) return null;

  const ordered = [...steps].sort((a, b) => a.sequence - b.sequence);
  const active = ordered.find((s) => s.status === 'active');
  const next = active
    ? ordered.find((s) => s.sequence > active.sequence && s.status === 'pending')
    : null;

  // Finished chains say so plainly rather than rendering an empty "current
  // stage" — a card that goes blank at the end reads as a data failure.
  if (!active) {
    const last = [...ordered].reverse().find((s) => s.acted_at) || ordered[ordered.length - 1];
    return (
      <div className="wsc wsc-done">
        <Row icon={CheckCircle2} label="Status">
          <strong>{status === 'archived' ? 'Archived' : 'Complete'}</strong>
          {' — no step is waiting.'}
        </Row>
        {last?.display_name && (
          <Row icon={UserCheck} label="Last action">
            {last.display_name} · {roleTypeLabel(last.role_type)}
          </Row>
        )}
      </div>
    );
  }

  const waiting = since(active.activated_at);
  return (
    <div className="wsc">
      <Row icon={Clock} label="Current stage">
        <strong>{roleTypeLabel(active.role_type)}</strong>
        {` · step ${active.sequence} of ${ordered.length}`}
      </Row>
      <Row icon={UserCheck} label="With">
        <strong>{active.display_name || active.assignee_name || '—'}</strong>
        {active.designation ? ` · ${active.designation}` : ''}
      </Row>
      {waiting && (
        <Row icon={Clock} label="Pending since">
          {waiting}
          {/* The exact timestamp stays available on hover: "3 days" is what
              you want at a glance, the date is what you want in a dispute. */}
          <span className="wsc-exact" title={new Date(active.activated_at).toLocaleString()}>
            {' '}({new Date(active.activated_at).toLocaleDateString()})
          </span>
        </Row>
      )}
      <Row icon={ArrowRight} label="Next">
        {next
          ? `${roleTypeLabel(next.role_type)} — ${next.display_name || next.assignee_name || '—'}`
          : 'Archive'}
      </Row>
    </div>
  );
};

export default WorkflowStatusCard;
