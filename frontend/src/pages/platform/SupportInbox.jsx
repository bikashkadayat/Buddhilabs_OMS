import React, { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Star, Paperclip, AlertTriangle, Clock } from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';
import { describeApiError } from '../../services/apiErrors';
import { STATUS_TONE, TICKET_CATEGORIES, when } from '../../utils/supportFormat';
import TicketOps from '../../components/platform/desk/TicketOps';
import SlaBoard from '../../components/platform/desk/SlaBoard';
import Teams from '../../components/platform/desk/Teams';
import KnownIssues from '../../components/platform/desk/KnownIssues';

/**
 * The support desk. Tickets by view (open, critical, unread, waiting,
 * overdue, mine, resolved, closed), filtered by organization, priority,
 * category and agent; one ticket at a time on the right with the whole
 * conversation, internal notes, assignment, escalation and the SLA clock.
 *
 * Feature requests and ratings have their own views: a feature request is
 * moved along a roadmap, not resolved against an SLA.
 *
 * Support Desk 3.0 adds the operations around the tickets: every ticket is
 * owned by a team (routed by category) and usually an agent; it can be
 * transferred, escalated, linked, given follow-up tasks and tied to a known
 * issue; internal notes @mention colleagues. The SLA board, Teams and Known
 * issues are sections of the same page.
 */
const SECTIONS = [['tickets', 'Tickets'], ['sla', 'SLA board'], ['teams', 'Teams'], ['issues', 'Known issues']];
const VIEWS = [
  ['open', 'Open', 'open'], ['my_teams', 'My teams', null], ['unassigned', 'Unassigned', 'unassigned'],
  ['critical', 'Critical', 'critical'], ['escalated', 'Escalated', 'escalated'], ['unread', 'Unread', 'unread'],
  ['waiting_customer', 'Waiting for customer', 'waiting_customer'], ['overdue', 'Overdue', 'overdue'],
  ['mine', 'Assigned to me', null], ['resolved', 'Resolved', 'resolved'], ['closed', 'Closed', 'closed'],
  ['feature', 'Feature requests', 'feature_requests_open'], ['feedback', 'Ratings', null],
];
const PRIORITIES = [['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']];
const STATUSES = [['open', 'Open'], ['in_progress', 'In progress'], ['waiting_customer', 'Waiting for customer'],
  ['resolved', 'Resolved'], ['closed', 'Closed']];
const ROADMAP = [['submitted', 'Submitted'], ['under_review', 'Under review'], ['planned', 'Planned'],
  ['in_development', 'In development'], ['completed', 'Completed']];

const paramsFor = (view, filters) => {
  const p = { ...filters };
  if (view === 'feature' || view === 'feedback') p.kind = view;
  else if (['resolved', 'closed', 'waiting_customer'].includes(view)) p.status = view;
  else p.view = view;
  return p;
};

const slaLabel = (t) => {
  if (t.kind === 'feature' || !t.sla_due_at || ['resolved', 'closed'].includes(t.status)) return null;
  if (t.sla_paused) return { text: 'SLA paused', tone: '' };
  const mins = Math.round((new Date(t.sla_due_at) - Date.now()) / 60000);
  if (mins < 0) return { text: `Overdue by ${Math.abs(Math.round(mins / 60))}h`, tone: 'is-bad' };
  return { text: mins < 120 ? `${mins}m left` : `${Math.round(mins / 60)}h left`, tone: mins < 120 ? 'is-warn' : '' };
};

const FileButton = ({ id, which, messageId, meta }) => (meta ? (
  <button type="button" className="sc-filelink" onClick={() => platformService.supportFile(id, which, messageId)
    .then((r) => window.open(URL.createObjectURL(r.data), '_blank', 'noopener'))}>
    <Paperclip size={13} aria-hidden="true" /> {meta.name}
  </button>
) : null);

/** Internal notes: @handles stand out, so a mention is seen. */
const withMentions = (body) => body.split(/(@[A-Za-z0-9][A-Za-z0-9._-]+)/g).map((part, i) => (
  part.startsWith('@') ? <strong key={i} className="dk-mention">{part}</strong> : part));

const Ticket = ({ id, agents, teams, issues, onChanged }) => {
  const [t, setT] = useState(null);
  const [text, setText] = useState('');
  const [internal, setInternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => platformService.supportTicket(id).then(({ data }) => setT(data)), [id]);
  useEffect(() => { setT(null); load(); }, [load]);

  const refresh = useCallback(async () => { await load(); onChanged(); }, [load, onChanged]);
  const patch = async (body) => {
    setBusy(true); setError('');
    try { await platformService.updateSupport(id, body); await load(); onChanged(); }
    catch (e) { setError(describeApiError(e, 'That didn’t save.')); }
    finally { setBusy(false); }
  };
  const send = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      await platformService.supportMessage(id, text, internal);
      setText(''); await load(); onChanged();
    } catch (e) { setError(describeApiError(e, 'That didn’t send.')); }
    finally { setBusy(false); }
  };

  if (!t) return <section className="sd-detail"><p className="pc-muted">Loading…</p></section>;
  const sla = slaLabel(t);
  const ctx = t.context || {};
  return (
    <section className="sd-detail" aria-label={`Ticket ${t.reference}`}>
      <header>
        <p className="sc-ref">{t.reference} · {t.category_display || t.kind_display}</p>
        <h2>{t.subject || t.message.slice(0, 80)}</h2>
        <p className="pc-muted">
          {t.organization_slug ? <Link to={`/platform/organizations/${t.organization_slug}`}>{t.organization_name}</Link> : 'platform'}
          {' · '}{t.submitted_by_name} &lt;{t.submitted_by_email}&gt; ({t.submitted_by_role || '—'}) · {when(t.created_at)}
        </p>
      </header>

      <dl className="sd-context">
        <dt>Page</dt><dd>{t.url || t.page || '—'}</dd>
        <dt>Browser</dt><dd>{ctx.browser || '—'} on {ctx.os || '—'} · {ctx.device || '—'} {ctx.viewport ? `· ${ctx.viewport}` : ''}</dd>
        <dt>Plan</dt><dd>{ctx.plan || '—'}</dd>
        <dt>Customer time</dt><dd>{ctx.client_time ? `${new Date(ctx.client_time).toLocaleString()} (${ctx.timezone || '?'})` : '—'}</dd>
      </dl>

      <div className="sd-controls">
        {t.kind === 'feature' ? (
          <label>Roadmap
            <select value={t.roadmap_status} disabled={busy} onChange={(e) => patch({ roadmap_status: e.target.value })}>
              {ROADMAP.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        ) : (
          <>
            <label>Status
              <select value={t.status} disabled={busy} onChange={(e) => patch({ status: e.target.value })}>
                {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label>Priority
              <select value={t.priority} disabled={busy} onChange={(e) => patch({ priority: e.target.value })}>
                {PRIORITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </>
        )}
        <label>Assigned
          <select value={t.assigned_to || ''} disabled={busy} onChange={(e) => patch({ assigned_to: e.target.value || null })}>
            <option value="">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        {t.kind !== 'feature' && !['resolved', 'closed'].includes(t.status) && (
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => patch({ escalate: true })}>
            <AlertTriangle size={14} aria-hidden="true" /> {t.escalation_level ? `Escalate to L${t.escalation_level + 1}` : 'Escalate'}
          </button>
        )}
        {sla && <span className={`sd-sla ${sla.tone}`}><Clock size={13} aria-hidden="true" /> {sla.text}</span>}
        {t.escalated && <span className="pf-chip pf-chip-warn">Escalated{t.escalation_level ? ` L${t.escalation_level}` : ''}</span>}
      </div>

      <TicketOps t={t} teams={teams} issues={issues} onChanged={refresh} />

      <ol className="sc-thread">
        <li className="sc-msg is-customer">
          <p className="sc-msg-meta"><strong>{t.submitted_by_name}</strong> · {when(t.created_at)}</p>
          <p className="sc-msg-body">{t.message}</p>
          <div className="sc-msg-files">
            <FileButton id={t.id} which="screenshot" meta={t.screenshot} />
            <FileButton id={t.id} which="attachment" meta={t.attachment} />
          </div>
        </li>
        {t.messages.map((m) => (
          <li key={m.id} className={`sc-msg is-${m.author_kind}${m.is_internal ? ' is-internal' : ''}`}>
            {m.author_kind === 'system' ? (
              <p className="sc-msg-system">{m.is_internal && <span className="sd-tag">Internal</span>} {m.body} · {when(m.created_at)}</p>
            ) : (
              <>
                <p className="sc-msg-meta">
                  {m.is_internal && <span className="sd-tag">Internal note</span>}
                  <strong>{m.author_name}</strong> · {when(m.created_at)}
                </p>
                {m.body && <p className="sc-msg-body">{m.is_internal ? withMentions(m.body) : m.body}</p>}
                <div className="sc-msg-files"><FileButton id={t.id} which="message" messageId={m.id} meta={m.attachment} /></div>
              </>
            )}
          </li>
        ))}
      </ol>

      {t.satisfaction_rating && (
        <p className="sd-csat">
          Customer rated support {t.satisfaction_rating}/5{t.satisfaction_comment && <>: “{t.satisfaction_comment}”</>}
        </p>
      )}

      <form className="sd-reply" onSubmit={send}>
        <div className="pf-tabs" role="tablist" aria-label="Reply or note">
          <button type="button" role="tab" aria-selected={!internal} className={`pf-tab${!internal ? ' is-active' : ''}`}
                  onClick={() => setInternal(false)}>Reply to customer</button>
          <button type="button" role="tab" aria-selected={internal} className={`pf-tab${internal ? ' is-active' : ''}`}
                  onClick={() => setInternal(true)}>Internal note</button>
        </div>
        <textarea rows={4} value={text} aria-label={internal ? 'Internal note' : 'Reply'}
                  placeholder={internal ? 'Only the support team sees this. Type @name to notify a colleague.' : `Emailed to ${t.submitted_by_email}.`}
                  onChange={(e) => setText(e.target.value)} />
        {error && <p className="pc-err" role="alert">{error}</p>}
        <div className="pc-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>
            {internal ? 'Add note' : 'Send reply'}
          </button>
          {!internal && t.kind !== 'feature' && !['resolved', 'closed'].includes(t.status) && (
            <button type="button" className="btn btn-ghost" disabled={busy}
                    onClick={() => patch({ status: 'waiting_customer' })}>Waiting for customer</button>
          )}
          {t.kind !== 'feature' && !['resolved', 'closed'].includes(t.status) && (
            <button type="button" className="btn btn-ghost" disabled={busy}
                    onClick={() => patch({ status: 'resolved' })}>Resolve</button>
          )}
        </div>
      </form>
    </section>
  );
};

const SupportInbox = () => {
  const [params, setParams] = useSearchParams();
  const section = params.get('section') || 'tickets';
  const [view, setView] = useState('open');
  const [filters, setFilters] = useState({});
  const [data, setData] = useState(null);
  const [agents, setAgents] = useState([]);
  const [teams, setTeams] = useState([]);
  const [issues, setIssues] = useState([]);
  const [selected, setSelected] = useState(params.get('ticket'));
  const [reload, setReload] = useState(0);

  const loadDesk = useCallback(() => Promise.all([
    platformService.supportAgents().then(({ data: d }) => setAgents(d)).catch(() => setAgents([])),
    platformService.supportTeams().then(({ data: d }) => setTeams(d)).catch(() => setTeams([])),
    platformService.knownIssues().then(({ data: d }) => setIssues(d)).catch(() => setIssues([])),
  ]), []);
  useEffect(() => { loadDesk(); }, [loadDesk, reload]);
  const changed = useCallback(() => setReload((n) => n + 1), []);
  const goSection = (key) => setParams(key === 'tickets' ? {} : { section: key });
  const openTicket = (id) => { setSelected(id); setParams({}); };
  useEffect(() => {
    platformService.supportInbox(paramsFor(view, filters))
      .then(({ data: d }) => setData(d)).catch(() => setData({ requests: [], summary: {} }));
  }, [view, filters, reload]);

  const s = data?.summary || {};
  const setFilter = (key) => (e) => setFilters((f) => {
    const next = { ...f, [key]: e.target.value };
    if (!e.target.value) delete next[key];
    return next;
  });
  const fb = data?.feedback_30d;
  const rows = data?.requests || [];

  return (
    <div className="page sd">
      <PageHeader breadcrumb="Customers" title="Support desk"
                  description={`Avg resolution ${s.avg_resolution_hours_30d ?? '—'}h · first reply ${s.avg_first_response_hours_30d ?? '—'}h · satisfaction ${s.csat_30d?.average ?? '—'}/5 (30 days)`} />
      <div className="sd-tiles">
        {[['Open', s.open], ['Waiting on team', s.waiting_team], ['Waiting for customer', s.waiting_customer],
          ['Critical', s.critical], ['Escalated', s.escalated], ['Overdue', s.overdue], ['Unassigned', s.unassigned],
          ['Resolved', s.resolved]].map(([label, n]) => (
          <div key={label} className={`sd-tile${['Overdue', 'Escalated'].includes(label) && n ? ' is-bad' : ''}`}><strong>{n ?? '—'}</strong><span>{label}</span></div>
        ))}
      </div>
      <div className="dk-sections" role="tablist" aria-label="Desk section">
        {SECTIONS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={section === key}
                  className={`cs-seg${section === key ? ' is-on' : ''}`} onClick={() => goSection(key)}>
            <span>{label}</span>
          </button>
        ))}
      </div>
      {section === 'sla' && <SlaBoard teams={teams} onOpen={openTicket} />}
      {section === 'teams' && <Teams teams={teams} agents={agents} onChanged={loadDesk} />}
      {section === 'issues' && <KnownIssues issues={issues} onChanged={loadDesk} />}
      {section === 'tickets' && (<>
      <div className="pf-tabs sd-views" role="tablist" aria-label="View">
        {VIEWS.map(([key, label, countKey]) => (
          <button key={key} type="button" role="tab" aria-selected={view === key}
                  className={`pf-tab${view === key ? ' is-active' : ''}`}
                  onClick={() => { setView(key); setSelected(null); setData(null); }}>
            {label}{countKey && s[countKey] ? ` (${s[countKey]})` : ''}
          </button>
        ))}
      </div>
      {view !== 'feedback' && (
        <div className="sd-filters">
          <input type="search" placeholder="Search subject, email, SUP-…" aria-label="Search tickets"
                 value={filters.q || ''} onChange={setFilter('q')} />
          <input placeholder="Organization address (slug)" aria-label="Organization"
                 value={filters.organization || ''} onChange={setFilter('organization')} />
          <select aria-label="Priority" value={filters.priority || ''} onChange={setFilter('priority')}>
            <option value="">Any priority</option>
            {PRIORITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Category" value={filters.category || ''} onChange={setFilter('category')}>
            <option value="">Any category</option>
            {TICKET_CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Team" value={filters.team || ''} onChange={setFilter('team')}>
            <option value="">Any team</option>
            <option value="none">No team</option>
            {teams.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          <select aria-label="Assigned agent" value={filters.assigned || ''} onChange={setFilter('assigned')}>
            <option value="">Any agent</option>
            <option value="none">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      )}

      {view === 'feedback' && fb?.count ? (
        <p className="pc-muted">Product ratings, last 30 days: {fb.average} / 5 from {fb.count}.</p>
      ) : null}

      {data === null ? <p className="pc-muted">Loading…</p> : rows.length === 0 && !selected ? (
        <EmptyState variant="cleared" title="Nothing here" body="No tickets match this view." />
      ) : (
        <div className={`sd-split${selected ? ' has-detail' : ''}`}>
          <ul className="sd-list">
            {rows.map((r) => {
              const sla = slaLabel(r);
              return (
                <li key={r.id}>
                  {r.kind === 'feedback' ? (
                    <div className="sd-row">
                      <span className="si-stars" aria-label={`${r.rating} out of 5`}>
                        {[1, 2, 3, 4, 5].map((n) => <Star key={n} size={14} className={n <= r.rating ? 'is-on' : ''} aria-hidden="true" />)}
                      </span>
                      <span><strong>{r.feature || 'Product'}</strong><small className="pc-muted"> · {r.organization_name} · {when(r.created_at)}</small>
                        {r.message && <small className="pc-muted"> — “{r.message}”</small>}</span>
                    </div>
                  ) : (
                    <button type="button" className={`sd-row${selected === r.id ? ' is-on' : ''}${r.unread ? ' is-unread' : ''}`}
                            onClick={() => setSelected(r.id)}>
                      <span className={`sd-prio is-${r.priority}`} title={r.priority_display} />
                      <span className="sd-row-main">
                        <strong>{r.unread && <span className="sc-dot" aria-label="Unread" />}{r.subject || r.message.slice(0, 70)}</strong>
                        <small className="pc-muted">{r.reference} · {r.organization_name || 'platform'} · {r.category_display} · {when(r.created_at)}
                          {r.team_name ? ` · ${r.team_name}` : ''}{r.assigned_to_name ? ` · ${r.assigned_to_name}` : ' · unassigned'}</small>
                      </span>
                      <span className="sd-row-side">
                        <span className={`pf-chip ${STATUS_TONE[r.status] || ''}`}>{r.kind === 'feature' ? r.roadmap_status_display : r.status_display}</span>
                        {sla && <small className={`sd-sla ${sla.tone}`}>{sla.text}</small>}
                        {r.escalation_level > 0 && <small className="sd-sla is-warn">L{r.escalation_level}</small>}
                      </span>
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {selected && <Ticket id={selected} agents={agents} teams={teams} issues={issues} onChanged={changed} />}
        </div>
      )}
      </>)}
    </div>
  );
};

export default SupportInbox;
