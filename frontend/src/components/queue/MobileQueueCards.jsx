import React from 'react';
import { Link } from 'react-router-dom';
import { waitingLabel, isOverdue } from '../../services/workQueue';
import { useRowAction } from './useRowAction';
import RemarkDialog from './RemarkDialog';
import { Check, X } from 'lucide-react';

/**
 * Queue items as cards, for one-handed use (Phase F).
 *
 * Layout only. The decision behaviour comes from useRowAction, the same hook
 * QueueRow uses, so a rejection needs a reason here exactly as it does on
 * desktop and the two cannot drift.
 *
 * Buttons are full-width and stacked rather than side by side: Approve and
 * Reject next to each other under a thumb is a mis-tap waiting to happen, and
 * the two outcomes are not symmetrical.
 */

const TYPE_LABEL = {
  task: 'TASK', appraisal: 'APPRAISAL', memo: 'MEMO', minute: 'MINUTE',
  circular: 'CIRCULAR', leave: 'LEAVE', asset: 'ASSET',
  attendance: 'ATTENDANCE',
};
const PAST_TENSE = {
  approve: 'Approved', reject: 'Rejected', acknowledge: 'Acknowledged', done: 'Done',
  review: 'Reviewed', recommend: 'Recommended', support: 'Supported',
};

const Card = ({ item, onAction }) => {
  const {
    remarkFor, remark, setRemark, remarkError, busy, start, submitRemark, cancelRemark,
    canSubmit, rules, touched, markTouched, precheck, blockedReason,
  } = useRowAction(item, onAction);

  const overdue = isOverdue(item);

  return (
    <li className={`mqc${overdue ? ' is-overdue' : ''}${item.resolved ? ' is-done' : ''}`}>
      <div className="mqc-top">
        <span className={`wq-tag wq-tag-${item.type}`}>{TYPE_LABEL[item.type] || item.type}</span>
        <span className={`mqc-age${overdue ? ' is-late' : ''}`}>{waitingLabel(item)}</span>
      </div>

      <Link to={item.href} className="mqc-title">{item.title}</Link>
      {item.subtitle && <p className="mqc-sub">{item.subtitle}</p>}
      {item.stepLine && <p className="wq-step" data-testid="queue-step">{item.stepLine}</p>}

      <p className="mqc-who">
        <span className="wq-av" aria-hidden="true">{item.requesterInitials}</span>
        {item.requester}
      </p>

      {item.error && <p className="wq-error" role="alert">{item.error}</p>}

      {item.resolved ? (
        <p className={`mqc-done${item.resolved.verb === 'reject' ? ' is-neg' : ''}`}>
          {item.resolved.verb === 'reject'
            ? <X size={14} aria-hidden="true" />
            : <Check size={14} aria-hidden="true" />}{' '}
          {PAST_TENSE[item.resolved.verb] || 'Done'}
        </p>
      ) : item.actions?.length ? (
        <div className="mqc-acts">
          {item.actions.map((a) => (
            <button
              key={a.verb}
              type="button"
              className={`mqc-btn${a.variant === 'primary' ? ' is-primary' : ''}`}
              disabled={busy}
              onClick={() => start(a)}
            >
              {a.label}
            </button>
          ))}
        </div>
      ) : (
        <Link to={item.href} className="mqc-btn mqc-open">Open</Link>
      )}

      {/* The comment dialog (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE). Rendered
          alongside the buttons rather than in their place, so the card keeps its
          shape underneath while the dialog is open. */}
      {remarkFor && (
        <RemarkDialog
          item={item} action={remarkFor} remark={remark} setRemark={setRemark}
          onSubmit={submitRemark} onCancel={cancelRemark} busy={busy}
          error={remarkError} canSubmit={canSubmit}
          rules={rules} touched={touched} markTouched={markTouched}
          precheck={precheck} blockedReason={blockedReason}
        />
      )}
    </li>
  );
};

const MobileQueueCards = ({ items = [], onAction }) => (
  <ul className="mqc-list">
    {items.map((item) => <Card key={item.id} item={item} onAction={onAction} />)}
  </ul>
);

export default MobileQueueCards;
