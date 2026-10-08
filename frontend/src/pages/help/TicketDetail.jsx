import React, { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ArrowLeft, Paperclip, Star, CheckCircle2 } from 'lucide-react';

import { supportService } from '../../services/supportService';
import { describeApiError } from '../../services/apiErrors';
import { STATUS_TONE, when } from '../../utils/supportFormat';

/**
 * One ticket: what was sent, the conversation, a reply box, and -- once it
 * is resolved -- "How was your support experience?". Staff internal notes
 * never reach this page; the server does not send them.
 */
const FileLink = ({ ticketId, which, messageId, meta }) => (meta ? (
  <button type="button" className="sc-filelink"
          onClick={() => supportService.openFile(ticketId, which, messageId)}>
    <Paperclip size={13} aria-hidden="true" /> {meta.name}
  </button>
) : null);

const Satisfaction = ({ ticket, onDone }) => {
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  if (!ticket.can_rate) {
    return ticket.satisfaction_rating ? (
      <p className="sc-rated" role="status"><CheckCircle2 size={15} aria-hidden="true" /> Thanks for rating our support {ticket.satisfaction_rating}/5.</p>
    ) : null;
  }
  const send = async () => {
    setBusy(true);
    try { onDone(await supportService.rate(ticket.id, rating, comment)); } catch { setBusy(false); }
  };
  return (
    <section className="sc-csat" aria-labelledby="sc-csat-h">
      <h2 id="sc-csat-h">How was your support experience?</h2>
      <span className="rt-stars">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" className={`rt-star${rating >= n ? ' is-on' : ''}`}
                  aria-label={`${n} out of 5`} aria-pressed={rating === n} onClick={() => setRating(n)}>
            <Star size={20} aria-hidden="true" />
          </button>
        ))}
      </span>
      {rating > 0 && (
        <>
          <label className="acc-field">
            <span>{rating < 4 ? 'What could we have done better?' : 'Anything to add? (optional)'}</span>
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={send}>Send rating</button>
        </>
      )}
    </section>
  );
};

const TicketDetail = () => {
  const { id } = useParams();
  const location = useLocation();
  const [ticket, setTicket] = useState(null);
  const [error, setError] = useState('');
  const [reply, setReply] = useState('');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const notice = location.state?.notice;

  const load = useCallback(() => supportService.ticket(id).then(setTicket)
    .catch((e) => setError(e?.response?.status === 404 ? 'That ticket doesn’t exist, or isn’t yours.'
      : describeApiError(e, 'The ticket could not be loaded.'))), [id]);
  useEffect(() => { load(); }, [load]);

  const send = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      await supportService.reply(id, reply, file);
      setReply(''); setFile(null);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.response?.data?.body?.[0]
        || describeApiError(e, 'Your reply didn’t send. Please try again.'));
    } finally { setBusy(false); }
  };

  const close = async () => {
    setBusy(true);
    try { setTicket(await supportService.close(id)); } finally { setBusy(false); }
  };

  if (!ticket) {
    return (
      <div className="page hc sc">
        <Link className="hc-back" to="/help/tickets"><ArrowLeft size={15} aria-hidden="true" /> My tickets</Link>
        {error ? <p className="acc-err" role="alert">{error}</p> : <p className="hc-muted">Loading…</p>}
      </div>
    );
  }

  return (
    <div className="page hc sc">
      <Link className="hc-back" to="/help/tickets"><ArrowLeft size={15} aria-hidden="true" /> My tickets</Link>
      {notice && <p className="sp-ok" role="status"><CheckCircle2 size={16} aria-hidden="true" /> {notice}</p>}
      <header className="sc-ticket-head">
        <div>
          <p className="sc-ref">{ticket.reference} · {ticket.category_display}</p>
          <h1 className="hc-h1">{ticket.subject || 'Support ticket'}</h1>
          <p className="hc-muted">Opened {when(ticket.created_at)} by {ticket.submitted_by_name}
            {ticket.page && <> · from <code>{ticket.page}</code></>}</p>
        </div>
        <span className={`pf-chip ${STATUS_TONE[ticket.status] || ''}`}>{ticket.status_display}</span>
      </header>

      <ol className="sc-thread">
        <li className="sc-msg is-customer">
          <p className="sc-msg-meta"><strong>{ticket.submitted_by_name}</strong> · {when(ticket.created_at)}</p>
          <p className="sc-msg-body">{ticket.message}</p>
          <div className="sc-msg-files">
            <FileLink ticketId={ticket.id} which="screenshot" meta={ticket.screenshot} />
            <FileLink ticketId={ticket.id} which="attachment" meta={ticket.attachment} />
          </div>
        </li>
        {ticket.messages.map((m) => (
          <li key={m.id} className={`sc-msg is-${m.author_kind}`}>
            {m.author_kind === 'system' ? (
              <p className="sc-msg-system">{m.body} <span className="hc-muted">· {when(m.created_at)}</span></p>
            ) : (
              <>
                <p className="sc-msg-meta"><strong>{m.author_kind === 'staff' ? `${m.author_name} (support)` : m.author_name}</strong> · {when(m.created_at)}</p>
                {m.body && <p className="sc-msg-body">{m.body}</p>}
                <div className="sc-msg-files">
                  <FileLink ticketId={ticket.id} which="message" messageId={m.id} meta={m.attachment} />
                </div>
              </>
            )}
          </li>
        ))}
      </ol>

      <Satisfaction ticket={ticket} onDone={setTicket} />

      {ticket.can_reply ? (
        <form className="sp-form" onSubmit={send}>
          <label className="acc-field">
            <span>{ticket.status === 'waiting_customer' ? 'We’re waiting for your reply' : 'Reply'}</span>
            <textarea rows={4} value={reply} onChange={(e) => setReply(e.target.value)} />
          </label>
          <div className="sc-files">
            <label className="sc-file">
              <Paperclip size={15} aria-hidden="true" />
              <span>{file ? file.name : 'Attach a file'}</span>
              <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
          </div>
          {error && <p className="acc-err" role="alert">{error}</p>}
          <div className="sc-actions">
            <button type="submit" className="btn btn-primary" disabled={busy || (!reply.trim() && !file)}>
              {ticket.status === 'resolved' ? 'Reply and reopen' : 'Send reply'}
            </button>
            {ticket.status !== 'closed' && (
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={close}>
                {ticket.status === 'resolved' ? 'Close ticket' : 'It’s sorted — close ticket'}
              </button>
            )}
          </div>
        </form>
      ) : (
        <p className="hc-muted">This ticket is closed. <Link to="/help/contact">Open a new one</Link> if you need more help.</p>
      )}
    </div>
  );
};

export default TicketDetail;
