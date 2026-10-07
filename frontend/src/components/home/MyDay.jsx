import React from 'react';
import { Link } from 'react-router-dom';
import { Check, CheckCircle2, X } from 'lucide-react';
import { useRowAction } from '../queue/useRowAction';
import RemarkDialog from '../queue/RemarkDialog';
import { EMPTY } from '../../services/emptyStates';
import {
  focusVerb, focusAction, timeLeft, verbTone,
} from './focus';

/**
 * My focus today (Phase 203 / blueprint §03, widget 5 — redesigned).
 *
 * The top five queue items (three on a phone), most urgent first, each with
 * ONE verb: the chip and the button say the same word. The decision behaviour
 * is useRowAction, the hook the full queue's rows use, so a memo review asks
 * for its remark here exactly as it does there and the two cannot drift.
 *
 * An item with no inline action (a task to start, a minute to sign off, a
 * review that needs a written reason) carries a link labelled with its verb
 * instead of a button — the row never offers an action the server would
 * refuse. Blueprint §04, the escalation rule.
 */

const PAST_TENSE = {
  approve: 'Approved', reject: 'Rejected', acknowledge: 'Acknowledged', done: 'Done',
  accept: 'Accepted', review: 'Reviewed', recommend: 'Recommended', support: 'Supported',
};

const Row = ({ item, onAction, primary }) => {
  const {
    remarkFor, remark, setRemark, remarkError, busy, start, submitRemark, cancelRemark,
    canSubmit, rules, touched, markTouched, precheck, blockedReason,
  } = useRowAction(item, onAction);

  const verb = focusVerb(item);
  const action = focusAction(item);
  const when = timeLeft(item);
  const btnClass = `hm-focus-btn${primary ? ' is-primary' : ''}`;
  // reference · status (the subtitle the queue already builds) · who it is
  // from, when the source named somebody. Nothing is fetched for this line.
  const meta = [
    item.subtitle,
    item.requester && item.requester !== 'Unknown' ? item.requester : null,
  ].filter(Boolean).join(' · ');

  return (
    <li className={`hm-focus-row${when.late ? ' is-late' : ''}${item.resolved ? ' is-done' : ''}`}>
      <span className={`hm-chip is-${verbTone(verb, when.late)}`}>{verb}</span>

      <div className="hm-focus-body">
        <Link to={item.href} className="hm-focus-t">{item.title}</Link>
        {meta && <p className="hm-focus-m">{meta}</p>}
        {item.stepLine && <p className="hm-focus-m" data-testid="queue-step">{item.stepLine}</p>}
        {item.error && (
          <p className="wq-error" role="alert">
            {item.error}{' '}
            {action && (
              <button type="button" className="wq-link" onClick={() => start(action)}>
                Try again
              </button>
            )}
          </p>
        )}
      </div>

      {when.label && (
        <span className={`hm-focus-when${when.late ? ' is-late' : ''}`}>{when.label}</span>
      )}

      {item.resolved ? (
        <span className={`hm-focus-done${item.resolved.verb === 'reject' ? ' is-neg' : ''}`}>
          {item.resolved.verb === 'reject'
            ? <X size={14} strokeWidth={1.75} aria-hidden="true" />
            : <Check size={14} strokeWidth={1.75} aria-hidden="true" />}{' '}
          {PAST_TENSE[item.resolved.verb] || 'Done'}
        </span>
      ) : action ? (
        <button type="button" className={btnClass} disabled={busy} onClick={() => start(action)}>
          {verb}
        </button>
      ) : (
        <Link to={item.href} className={btnClass}>{verb}</Link>
      )}

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

const MyDay = ({ items = [], total = 0, loading = false, onAction }) => {
  // The first overdue row gets the filled button; when nothing is late, the
  // most urgent row (the first) does. One primary per card, never two.
  const primaryIndex = Math.max(0, items.findIndex((i) => timeLeft(i).late));

  return (
    <section className="hm-sec" aria-labelledby="hm-focus-h">
      <div className="hm-sec-h">
        <h2 id="hm-focus-h">My focus today</h2>
        <span className="hm-sec-sub">Most urgent first</span>
        <Link to="/queue" className="hm-sec-link">Open my queue →</Link>
      </div>

      <div className="hm-card hm-focus">
        {loading ? (
          <ul className="hm-focus-list" aria-busy="true">
            {Array.from({ length: 3 }, (_, i) => (
              <li className="hm-focus-row is-skeleton" key={i} aria-hidden="true">
                <span className="hm-skel hm-skel-chip" />
                <div className="hm-focus-body">
                  <span className="hm-skel" style={{ width: '52%' }} />
                  <span className="hm-skel hm-skel-sm" style={{ width: '30%' }} />
                </div>
                <span className="hm-skel hm-skel-sm" style={{ width: 60 }} />
                <span className="hm-skel hm-skel-btn" />
              </li>
            ))}
          </ul>
        ) : items.length === 0 ? (
          <div className="hm-clear">
            <CheckCircle2 size={18} strokeWidth={1.75} className="hm-clear-ic" aria-hidden="true" />
            <div>
              <strong>{EMPTY.nothingWaiting}</strong>
              <p>Every approval, review and acknowledgement assigned to you is done.</p>
            </div>
          </div>
        ) : (
          <>
            <ul className="hm-focus-list">
              {items.map((item, i) => (
                <Row key={item.id} item={item} onAction={onAction} primary={i === primaryIndex} />
              ))}
            </ul>
            {total > items.length && (
              <Link to="/queue" className="hm-more">
                {total - items.length} more in your queue →
              </Link>
            )}
          </>
        )}
      </div>
    </section>
  );
};

export default MyDay;
