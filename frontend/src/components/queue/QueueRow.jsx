import React from 'react';
import { Link } from 'react-router-dom';
import { waitingLabel, isOverdue } from '../../services/workQueue';
import { useRowAction } from './useRowAction';
import RemarkDialog from './RemarkDialog';
import { Check, X } from 'lucide-react';

/**
 * One row of the Work Queue (Phase 203 / blueprint §04).
 *
 * Also used by Home's "My Day" via `dense`, so the two can never drift into
 * showing the same item differently.
 *
 * A remark is asked for in a DIALOG (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).
 * It used to be an inline field, shown for rejections only - which is how every
 * memo "Approve" went out with no comment and came back as a status code, because
 * every memo step requires one. The dialog names the item it is about, enforces
 * the minimum length before sending, and keeps the server's message and the typed
 * text on screen if the send is still refused. See RemarkDialog.
 */

const TYPE_LABEL = {
  task: 'TASK',
  appraisal: 'APPRAISAL',
  memo: 'MEMO',
  minute: 'MINUTE',
  circular: 'CIRCULAR',
  leave: 'LEAVE',
  asset: 'ASSET',
  attendance: 'ATTENDANCE',
};

const PAST_TENSE = {
  approve: 'Approved',
  reject: 'Rejected',
  acknowledge: 'Acknowledged',
  done: 'Done',
  // The memo roles, now that the button names the real step.
  review: 'Reviewed',
  recommend: 'Recommended',
  support: 'Supported',
};

const QueueRow = ({ item, onAction, dense = false }) => {
  // Shared with MobileQueueCards, so a decision behaves identically in both.
  const {
    remarkFor, remark, setRemark, remarkError, busy,
    start: onActionClick, submitRemark, cancelRemark, canSubmit,
    rules, touched, markTouched, precheck, blockedReason,
  } = useRowAction(item, onAction);

  const overdue = isOverdue(item);
  const waiting = waitingLabel(item);

  return (
    <div
      className={`wq-row${dense ? ' is-dense' : ''}${overdue ? ' is-overdue' : ''}${item.resolved ? ' is-done' : ''}`}
    >
      <span className={`wq-tag wq-tag-${item.type}`}>{TYPE_LABEL[item.type] || item.type}</span>

      <div className="wq-main">
        <Link to={item.href} className="wq-title">{item.title}</Link>
        {item.subtitle && <div className="wq-sub">{item.subtitle}</div>}
        {/* Your role and where the item sits in its chain (Phase
            MEMO-QUEUE-UX-HARDENING), before any button is pressed. */}
        {item.stepLine && <div className="wq-step" data-testid="queue-step">{item.stepLine}</div>}
        {item.error && (
          <div className="wq-error" role="alert">
            {item.error} <button type="button" className="wq-link" onClick={() => onActionClick(item.actions[0])}>Try again</button>
          </div>
        )}
      </div>

      {!dense && (
        <div className="wq-who">
          <span className="wq-av" aria-hidden="true">{item.requesterInitials}</span>
          <span className="wq-who-name">{item.requester}</span>
        </div>
      )}

      <span className={`wq-age${overdue ? ' is-late' : ''}`}>{waiting}</span>

      <div className="wq-actions">
        {item.resolved ? (
          <span className={`wq-done${item.resolved.verb === 'reject' ? ' is-neg' : ''}`}>
            {item.resolved.verb === 'reject'
              ? <X size={14} aria-hidden="true" />
              : <Check size={14} aria-hidden="true" />}{' '}
            {PAST_TENSE[item.resolved.verb] || 'Done'}
          </span>
        ) : item.actions?.length ? (
          item.actions.map((action) => (
            <button
              key={action.verb}
              type="button"
              className={`wq-btn${action.variant === 'primary' ? ' is-primary' : ''}`}
              disabled={busy}
              onClick={() => onActionClick(action)}
            >
              {action.label}
            </button>
          ))
        ) : (
          // No inline action by design - this decision needs the record in front
          // of it. Blueprint §04, the escalation rule.
          <Link to={item.href} className="wq-btn">Open</Link>
        )}
      </div>

      {remarkFor && (
        <RemarkDialog
          item={item} action={remarkFor} remark={remark} setRemark={setRemark}
          onSubmit={submitRemark} onCancel={cancelRemark} busy={busy}
          error={remarkError} canSubmit={canSubmit}
          rules={rules} touched={touched} markTouched={markTouched}
          precheck={precheck} blockedReason={blockedReason}
        />
      )}
    </div>
  );
};

export default QueueRow;
