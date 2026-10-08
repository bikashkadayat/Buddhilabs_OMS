import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { platformService } from '../../../services/platformService';
import { describeApiError } from '../../../services/apiErrors';
import { TASK_KINDS, day } from './format';

/**
 * Part 4: the platform team's customer-success tasks. Shown for every
 * customer here, or for one (organization prop) on its detail page.
 */
const Tasks = ({ organization, agents: given }) => {
  const [loaded, setLoaded] = useState([]);
  useEffect(() => {
    if (!given) platformService.supportAgents().then(({ data }) => setLoaded(data)).catch(() => setLoaded([]));
  }, [given]);
  const agents = given || loaded;
  const [rows, setRows] = useState(null);
  const [show, setShow] = useState('open');
  const [form, setForm] = useState({ kind: 'follow_up', title: '', organization: organization || '', due_date: '', assigned_to: '' });
  const [error, setError] = useState('');
  const load = useCallback(() => platformService.successTasks({
    ...(organization ? { organization } : {}), ...(show === 'all' ? {} : { status: show }),
  }).then(({ data }) => setRows(data)).catch(() => setRows([])), [organization, show]);
  useEffect(() => { load(); }, [load]);

  const add = async (e) => {
    e.preventDefault(); setError('');
    try {
      await platformService.createSuccessTask({ ...form, assigned_to: form.assigned_to || null, due_date: form.due_date || null });
      setForm((f) => ({ ...f, title: '' })); load();
    } catch (err) { setError(err?.response?.data?.detail || describeApiError(err, 'The task could not be added.')); }
  };
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const update = (id, body) => platformService.updateSuccessTask(id, body).then(load);

  return (
    <div>
      <form className="sd-filters" onSubmit={add} aria-label="New customer success task">
        <select aria-label="Task type" value={form.kind} onChange={set('kind')}>
          {TASK_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {!organization && <input aria-label="Organization (slug)" required placeholder="Organization (slug)" value={form.organization} onChange={set('organization')} />}
        <input aria-label="Task title" placeholder="What needs doing" value={form.title} onChange={set('title')} />
        <input aria-label="Due date" type="date" value={form.due_date} onChange={set('due_date')} />
        <select aria-label="Owner" value={form.assigned_to} onChange={set('assigned_to')}>
          <option value="">Unassigned</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button type="submit" className="btn btn-primary btn-sm">Add task</button>
      </form>
      {error && <p className="pc-err" role="alert">{error}</p>}
      <div className="pf-tabs" role="tablist" aria-label="Show">
        {[['open', 'Open'], ['done', 'Done'], ['all', 'All']].map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={show === k} className={`pf-tab${show === k ? ' is-active' : ''}`} onClick={() => setShow(k)}>{l}</button>
        ))}
      </div>
      {rows === null ? <p className="pc-muted">Loading…</p> : rows.length === 0 ? <p className="pc-muted">No tasks here.</p> : (
        <ul className="sc-list">
          {rows.map((t) => (
            <li key={t.id} className={`cs-task${t.overdue ? ' is-overdue' : ''}`}>
              <div>
                <strong>{t.kind_display}: {t.title}</strong>
                <small className="pc-muted">
                  {!organization && <><Link to={`/platform/organizations/${t.organization}`}>{t.organization_name}</Link> · </>}
                  due {day(t.due_date)}{t.overdue ? ' (overdue)' : ''} · {t.assigned_to_name || 'unassigned'}
                  {t.auto_reason && !t.campaign && ' · opened automatically'}
                  {t.campaign_name && ` · campaign: ${t.campaign_name}`}
                  {t.ticket_reference && <> · <Link to={`/platform/support?ticket=${t.ticket}`}>{t.ticket_reference}</Link></>}
                </small>
                {t.notes && <p className="pc-note">{t.notes}</p>}
              </div>
              <div className="pc-actions">
                {t.status === 'open' ? (
                  <>
                    <button type="button" className="btn btn-primary btn-xs" onClick={() => update(t.id, { status: 'done' })}>Done</button>
                    <button type="button" className="btn btn-ghost btn-xs" onClick={() => update(t.id, { status: 'cancelled' })}>Cancel</button>
                  </>
                ) : <span className="pf-chip pf-chip-mute">{t.status_display}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default Tasks;
