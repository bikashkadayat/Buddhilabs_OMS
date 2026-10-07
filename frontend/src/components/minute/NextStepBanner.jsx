import React from 'react';
import {
  CheckCircle2, Clock, FileEdit, Send, UserCheck,
} from 'lucide-react';

/**
 * One strip at the top of a minute that answers the three questions a reader has: what
 * state is this in, is anything wanted from me, and what happens next.
 *
 * The cases are ordered by who is being addressed: anything wanted FROM THE READER
 * comes first, then what the minute is waiting on, then its resting state. Every
 * branch is driven by the server's own capability flags, so the banner cannot offer an
 * action the API would refuse.
 *
 * The two submit buttons are the manual's own (p.7) and appear together on a draft,
 * because the manual puts them side by side: draft review is optional.
 *
 * @param {{ minute:Object, busy?:boolean, onSendForReview:()=>void,
 *           onSendForAcknowledgement:()=>void, onReturnReview:()=>void,
 *           onAcknowledge:()=>void, onEdit:()=>void }} props
 */
const stamp = (iso) => (iso
  ? new Date(iso).toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: '2-digit' })
  : '');

const NextStepBanner = ({
  minute, busy = false, onSendForReview, onSendForAcknowledgement, onReturnReview,
  onAcknowledge, onEdit,
}) => {
  const ack = minute.acknowledgement;

  let tone = 'muted';
  let Icon = Clock;
  let title = minute.status_label;
  let text = null;
  let actions = null;

  if (minute.can_acknowledge) {
    tone = 'action';
    Icon = UserCheck;
    title = 'Please acknowledge this minute';
    text = 'You are recorded as present at this meeting. Acknowledging confirms you '
      + 'have read the record of it.';
    actions = (
      <button type="button" className="lr-btn lr-btn-primary" disabled={busy}
        onClick={onAcknowledge}>
        <UserCheck size={14} /> Acknowledge
      </button>
    );
  } else if (minute.can_return_review) {
    tone = 'action';
    Icon = FileEdit;
    title = 'This draft is with you for review';
    text = 'Edit it if you need to, then submit it for acknowledgement — or send it '
      + 'back to the author.';
    actions = (
      <>
        {minute.can_send_for_acknowledgement && (
          <button type="button" className="lr-btn lr-btn-primary" disabled={busy}
            onClick={onSendForAcknowledgement}>
            <Send size={14} /> Submit for Acknowledge
          </button>
        )}
        <button type="button" className="lr-btn" disabled={busy}
          onClick={onReturnReview}>
          Return to author
        </button>
      </>
    );
  } else if (minute.status === 'draft') {
    const canReview = minute.can_send_for_review;
    const canAck = minute.can_send_for_acknowledgement;
    tone = 'info';
    Icon = FileEdit;
    title = 'This minute is a draft';
    if (!canAck) {
      text = 'Add at least one member present before submitting it — they are who is '
        + 'asked to acknowledge.';
    } else if (canReview) {
      text = 'Send it to the FRO for a draft review first, or submit it straight for '
        + 'acknowledgement.';
    } else {
      text = 'Submit it when you are ready. Add an FRO if you want a draft review '
        + 'first.';
    }
    actions = (canReview || canAck) ? (
      <>
        {canReview && (
          <button type="button" className="lr-btn" disabled={busy}
            onClick={onSendForReview}>
            <Send size={14} /> Submit for Draft Review
          </button>
        )}
        {canAck && (
          <button type="button" className="lr-btn lr-btn-primary" disabled={busy}
            onClick={onSendForAcknowledgement}>
            <Send size={14} /> Submit for Acknowledge
          </button>
        )}
      </>
    ) : (
      <button type="button" className="lr-btn" onClick={onEdit}>
        <FileEdit size={14} /> Edit minute
      </button>
    );
  } else if (minute.status === 'draft_for_review') {
    tone = 'wait';
    Icon = Clock;
    title = `With ${minute.fro_name || 'the FRO'} for draft review`;
    text = 'Nothing is needed from you right now.';
  } else if (minute.status === 'pending_acknowledgement') {
    tone = 'wait';
    Icon = Clock;
    title = 'Waiting for members to acknowledge';
    text = ack?.total
      ? `${ack.acknowledged} of ${ack.total} members present have acknowledged. `
        + 'It archives itself once they all have.'
      : null;
  } else if (minute.status === 'archived') {
    tone = 'ok';
    Icon = CheckCircle2;
    title = 'Acknowledged and archived';
    text = [
      ack?.total ? `All ${ack.total} members present acknowledged it` : null,
      minute.archived_at ? `archived on ${stamp(minute.archived_at)}` : null,
    ].filter(Boolean).join(' · ') || null;
  }

  return (
    <div className={`min-next is-${tone}`} role="status">
      <span className="min-next-ico" aria-hidden="true"><Icon size={18} /></span>
      <span className="min-next-body">
        <span className="min-next-title">{title}</span>
        {text && <span className="min-next-text">{text}</span>}
      </span>
      {actions && <span className="min-next-actions">{actions}</span>}
    </div>
  );
};

export default NextStepBanner;
