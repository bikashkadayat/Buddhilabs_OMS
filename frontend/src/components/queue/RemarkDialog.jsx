import React, { useEffect, useRef } from 'react';

import Modal from '../common/Modal';

/**
 * The remark an action needs before it may be sent
 * (Phases MEMO-ACT-ENDPOINT-400-ROOT-CAUSE, MEMO-QUEUE-UX-HARDENING).
 *
 * Every memo role - Reviewer, Recommender, Supporter, Approver - and every
 * rejection comes through here. The contract:
 *
 *   - WHERE YOU ARE: your role, which step of how many, and what each decision
 *     leads to - the next person by name, or "approved and filed", or back to the
 *     author. Acting without knowing where the memo goes next is how a reviewer
 *     ends up rejecting something they meant to pass on.
 *
 *   - THE RULE BEFORE THE ATTEMPT: "Remarks *", a live counter against the
 *     minimum, and the send button disabled until the remark is valid - counted
 *     the way the server counts. The server is never the first place a length
 *     problem appears.
 *
 *   - A STALE ITEM SAYS SO ON OPEN: if the memo moved on or was withdrawn since
 *     the queue loaded, the dialog says that before anything is typed.
 *
 *   - IF THE SERVER STILL REFUSES (a race after the check), its own sentence is
 *     shown here and the typed remark is kept.
 *
 * Layout only: every rule comes from `useRowAction`.
 */
const RemarkDialog = ({
  item, action, remark, setRemark, onSubmit, onCancel, busy, error, canSubmit,
  rules, touched, markTouched, precheck = 'ready', blockedReason = '',
}) => {
  const fieldRef = useRef(null);
  const fieldId = `remark-${item.id}`;
  const counterId = `${fieldId}-counter`;
  const feedbackId = `${fieldId}-feedback`;
  const flow = action.workflow;
  const blocked = precheck === 'blocked';
  const checking = precheck === 'checking';

  // Modal focuses its first control (the Close button); the field is what the
  // person came to fill in, so it takes focus once the check allows typing.
  useEffect(() => {
    if (precheck !== 'ready') return undefined;
    const frame = requestAnimationFrame(() => fieldRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [precheck]);

  // Feedback is shown once the person has started typing or tried to send, not
  // the instant the dialog opens - an error on an empty field nobody has touched
  // reads as an accusation.
  const showFeedback = touched || !rules.empty;
  let feedback = '';
  let tone = 'ok';
  if (blocked) { feedback = ''; } else if (rules.empty && touched) {
    feedback = 'Remarks are required.'; tone = 'error';
  } else if (!rules.valid && showFeedback) {
    feedback = `At least ${rules.min} characters are required — ${rules.missing} more to go.`;
    tone = 'warn';
  } else if (rules.valid) {
    feedback = 'Looks good.';
  }

  const isReject = action.verb === 'reject';
  const outcome = isReject
    ? (flow?.authorName ? `Returned to ${flow.authorName} to revise.` : 'Returned to the author to revise.')
    : flow?.isFinalStep
      ? 'The memo is approved and filed.'
      : flow?.next
        ? `Moves to ${flow.next.roleLabel} — ${flow.next.name}.`
        : '';

  return (
    <Modal
      title={action.dialogTitle || action.label}
      onClose={busy ? undefined : onCancel}
      footer={(
        <>
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onCancel} disabled={busy}>
            {/* Not "Close": the header already has a Close button, and two
                controls with one name are indistinguishable to a screen reader. */}
            {blocked ? 'Back to queue' : 'Cancel'}
          </button>
          {!blocked && (
            <button type="submit" form={`${fieldId}-form`}
              className={`lr-btn ${isReject ? 'lr-btn-danger' : 'lr-btn-primary'}`}
              disabled={!canSubmit}
              aria-describedby={feedbackId}>
              {busy ? 'Sending…' : checking ? 'Checking…' : action.label}
            </button>
          )}
        </>
      )}
    >
      <form id={`${fieldId}-form`} onSubmit={onSubmit} data-testid="remark-dialog" noValidate>
        <p className="lr-page-sub" style={{ marginTop: 0 }}>{item.title}</p>

        {/* Hidden once the check finds the item gone: "Moves to Recommender"
            describes a future that will now never happen. */}
        {flow && !blocked && (
          <dl className="wq-flow" data-testid="remark-dialog-flow">
            <div><dt>Your role</dt><dd>{flow.roleLabel}</dd></div>
            <div><dt>Current step</dt><dd>Step {flow.position} of {flow.total}</dd></div>
            <div><dt>{isReject ? 'If rejected' : 'Next step'}</dt><dd>{outcome}</dd></div>
          </dl>
        )}

        {checking && (
          <p className="wq-flow-note" role="status">Confirming this is still waiting on you…</p>
        )}
        {blocked && (
          <p className="wq-error" role="alert" data-testid="remark-dialog-blocked">
            {blockedReason} Closing this refreshes your queue.
          </p>
        )}

        {!blocked && (
          <>
            <div className="lr-field">
              <label htmlFor={fieldId} className="wq-remark-label">
                Remarks <span className="wq-required" aria-hidden="true">*</span>
                <span className="sr-only"> (required)</span>
              </label>
              <textarea
                id={fieldId}
                ref={fieldRef}
                rows={4}
                value={remark}
                required
                onChange={(e) => setRemark(e.target.value)}
                onBlur={markTouched}
                placeholder={action.remarkPlaceholder
                  || (isReject ? 'Briefly, so the requester knows what to change' : '')}
                aria-describedby={`${counterId} ${feedbackId}`}
                aria-invalid={(touched && !rules.valid) || Boolean(error)}
                disabled={busy || checking}
              />
            </div>
            <div className="wq-remark-meta">
              <span id={feedbackId} className={`wq-remark-feedback is-${tone}`} aria-live="polite">
                {feedback}
              </span>
              <span id={counterId} className={`wq-remark-counter${rules.valid ? ' is-valid' : ''}`}>
                {rules.length} / {rules.min} minimum
              </span>
            </div>
          </>
        )}
        {error && <p className="wq-error" role="alert">{error}</p>}
      </form>
    </Modal>
  );
};

export default RemarkDialog;
