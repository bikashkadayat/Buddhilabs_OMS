import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRightLeft, Link2, ListPlus, Bug, X } from 'lucide-react';

import { platformService } from '../../../services/platformService';
import { describeApiError } from '../../../services/apiErrors';
import HealthScore from '../success/HealthScore';
import { TASK_KINDS, day } from '../success/format';
import { when } from '../../../utils/supportFormat';

/**
 * The internal side of one ticket, under its controls: who owns it (team,
 * transfer), the known issue behind it, tickets about the same thing, the
 * follow-up tasks it has opened, and the customer's story so far. None of
 * this is ever shown to the customer.
 */
const TICKET_TASKS = TASK_KINDS.filter(([k]) => ['follow_up', 'training', 'domain_setup',
  'payment_verification', 'call'].includes(k));

const TicketOps = ({ t, teams, issues, onChanged }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [transfer, setTransfer] = useState({ team: '', note: '' });
  const [linkRef, setLinkRef] = useState('');
  const [task, setTask] = useState({ kind: 'follow_up', title: '' });

  const act = async (fn, after) => {
    setBusy(true); setError('');
    try { await fn(); if (after) after(); await onChanged(); }
    catch (e) { setError(describeApiError(e, 'That didn’t save.')); }
    finally { setBusy(false); }
  };
  const customer = t.customer;
  const openIssues = (issues || []).filter((i) => i.state !== 'fixed' || i.id === t.known_issue?.id);

  return (
    <div className="dk-ops" aria-label="Internal">
      {error && <p className="pc-err" role="alert">{error}</p>}

      <section className="dk-block" aria-labelledby={`own-${t.id}`}>
        <h3 id={`own-${t.id}`} className="dk-h">Ownership</h3>
        <p className="dk-line">
          <strong>{t.team_name || 'No team'}</strong>
          <span className="pc-muted"> · {t.assigned_to_name || 'not picked up yet'}</span>
          {t.escalation_level > 0 && (
            <span className="pf-chip pf-chip-warn">Escalation L{t.escalation_level}{t.escalated_at ? ` · ${when(t.escalated_at)}` : ''}</span>
          )}
        </p>
        <form className="dk-inline" onSubmit={(e) => {
          e.preventDefault();
          act(() => platformService.transferTicket(t.id, transfer), () => setTransfer({ team: '', note: '' }));
        }}>
          <select aria-label="Transfer to team" value={transfer.team} required
                  onChange={(e) => setTransfer((v) => ({ ...v, team: e.target.value }))}>
            <option value="">Transfer to…</option>
            {teams.filter((x) => x.is_active && x.id !== t.team).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          <input aria-label="Transfer note" placeholder="Why (optional)" value={transfer.note}
                 onChange={(e) => setTransfer((v) => ({ ...v, note: e.target.value }))} />
          <button type="submit" className="btn btn-ghost btn-sm" disabled={busy || !transfer.team}>
            <ArrowRightLeft size={14} aria-hidden="true" /> Transfer
          </button>
        </form>
      </section>

      <section className="dk-block" aria-labelledby={`ki-${t.id}`}>
        <h3 id={`ki-${t.id}`} className="dk-h">Known issue</h3>
        <div className="dk-inline">
          <select aria-label="Known issue" value={t.known_issue?.id || ''} disabled={busy}
                  onChange={(e) => act(() => platformService.updateSupport(t.id, { known_issue: e.target.value || null }))}>
            <option value="">None</option>
            {openIssues.map((i) => <option key={i.id} value={i.id}>{i.title} ({i.state_display})</option>)}
          </select>
          {!t.known_issue && (
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => act(() => platformService.createKnownIssue({ from_ticket: t.id }))}>
              <Bug size={14} aria-hidden="true" /> New known issue from this ticket
            </button>
          )}
        </div>
      </section>

      <section className="dk-block" aria-labelledby={`ln-${t.id}`}>
        <h3 id={`ln-${t.id}`} className="dk-h">Linked tickets</h3>
        {t.linked?.length > 0 && (
          <ul className="dk-chips">
            {t.linked.map((l) => (
              <li key={l.id}>
                <span>{l.reference} · {l.subject || l.organization} · {l.status_display}</span>
                <button type="button" className="dk-x" aria-label={`Unlink ${l.reference}`} disabled={busy}
                        onClick={() => act(() => platformService.linkTicket(t.id, l.id, true))}><X size={12} aria-hidden="true" /></button>
              </li>
            ))}
          </ul>
        )}
        <form className="dk-inline" onSubmit={(e) => {
          e.preventDefault();
          act(() => platformService.linkTicket(t.id, linkRef.trim()), () => setLinkRef(''));
        }}>
          <input aria-label="Ticket to link" placeholder="SUP-000123" value={linkRef}
                 onChange={(e) => setLinkRef(e.target.value)} />
          <button type="submit" className="btn btn-ghost btn-sm" disabled={busy || !linkRef.trim()}>
            <Link2 size={14} aria-hidden="true" /> Link
          </button>
        </form>
      </section>

      {t.organization_slug && (
        <section className="dk-block" aria-labelledby={`tk-${t.id}`}>
          <h3 id={`tk-${t.id}`} className="dk-h">Follow-up tasks</h3>
          {t.tasks?.length > 0 && (
            <ul className="dk-tasks">
              {t.tasks.map((x) => (
                <li key={x.id} className={x.overdue ? 'is-overdue' : ''}>
                  <span className={x.status === 'done' ? 'cs-done' : ''}>{x.kind_display}: {x.title}</span>
                  <small className="pc-muted">{x.status_display} · due {day(x.due_date)} · {x.assigned_to_name || 'unassigned'}</small>
                </li>
              ))}
            </ul>
          )}
          <form className="dk-inline" onSubmit={(e) => {
            e.preventDefault();
            act(() => platformService.createTicketTask(t.id, task), () => setTask({ kind: task.kind, title: '' }));
          }}>
            <select aria-label="Task type" value={task.kind} onChange={(e) => setTask((v) => ({ ...v, kind: e.target.value }))}>
              {TICKET_TASKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <input aria-label="Task title" placeholder="What needs doing (optional)" value={task.title}
                   onChange={(e) => setTask((v) => ({ ...v, title: e.target.value }))} />
            <button type="submit" className="btn btn-ghost btn-sm" disabled={busy}>
              <ListPlus size={14} aria-hidden="true" /> Add task
            </button>
          </form>
        </section>
      )}

      {customer && (
        <section className="dk-block dk-story" aria-labelledby={`cu-${t.id}`}>
          <h3 id={`cu-${t.id}`} className="dk-h">Customer story</h3>
          <div className="dk-story-head">
            {customer.health != null && <HealthScore org={customer} />}
            <div>
              <Link className="pc-org" to={`/platform/organizations/${customer.slug}#success`}>{customer.name}</Link>
              <p className="pc-muted">{customer.plan || 'no plan'} · {customer.subscription_status || '—'} · {customer.open_tickets} open ticket{customer.open_tickets === 1 ? '' : 's'}</p>
              {customer.reasons?.length > 0 && <p className="pc-muted">{customer.reasons.map((r) => r.text).join(' · ')}</p>}
            </div>
          </div>
          <ol className="cs-timeline dk-timeline">
            {customer.timeline.map((e) => (
              <li key={`${e.at}-${e.kind}-${e.title}`}>
                <time dateTime={e.at}>{when(e.at)}</time>
                <span>{e.title}{e.detail ? <span className="pc-muted"> — {e.detail}</span> : null}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
};

export default TicketOps;
