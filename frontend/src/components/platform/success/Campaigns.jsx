import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Megaphone } from 'lucide-react';

import { platformService } from '../../../services/platformService';
import { describeApiError } from '../../../services/apiErrors';
import EmptyState from '../../common/EmptyState';
import { TASK_KINDS, day } from './format';

/**
 * Proactive outreach: pick a segment (inactive, low health, trial, near
 * renewal), see exactly who is in it, and launch -- one task per customer,
 * on the Tasks list, tracked here to done. A customer already being called
 * from an open campaign on the same segment is skipped.
 */
const Campaigns = ({ agents }) => {
  const [data, setData] = useState(null);
  const [segment, setSegment] = useState('');
  const [preview, setPreview] = useState(null);
  const [form, setForm] = useState({ task_title: '', task_kind: 'outreach', assigned_to: '', due_in_days: 7, notes: '' });
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  const load = useCallback(() => platformService.successCampaigns()
    .then(({ data: d }) => setData(d)).catch(() => setError('Campaigns couldn’t be loaded.')), []);
  useEffect(() => { load(); }, [load]);
  const choose = (key) => {
    setSegment(key); setPreview(null); setDone('');
    const seg = data?.segments.find((x) => x.key === key);
    setForm((f) => ({ ...f, task_title: seg?.default_title || '' }));
    platformService.successCampaigns(key).then(({ data: d }) => setPreview(d.organizations)).catch(() => setPreview([]));
  };

  if (error && !data) return <p className="pc-err" role="alert">{error}</p>;
  if (!data) return <p className="pc-muted">Loading…</p>;
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const launch = async (e) => {
    e.preventDefault(); setError(''); setDone('');
    try {
      const { data: c } = await platformService.createCampaign({ segment, ...form });
      setDone(`${c.name}: ${c.created} task${c.created === 1 ? '' : 's'} opened${c.skipped ? `, ${c.skipped} skipped (already being contacted)` : ''}.`);
      setSegment(''); await load();
    } catch (err) { setError(describeApiError(err, 'That didn’t launch.')); }
  };

  return (
    <div className="dk-campaigns">
      <div className="cs-segments" role="tablist" aria-label="Campaign segment">
        {data.segments.map((x) => (
          <button key={x.key} type="button" role="tab" aria-selected={segment === x.key}
                  className={`cs-seg${segment === x.key ? ' is-on' : ''}`} onClick={() => choose(x.key)}>
            <b>{x.count}</b><span>{x.label}</span>
          </button>
        ))}
      </div>
      {done && <p className="pc-ok" role="status">{done}</p>}
      {error && <p className="pc-err" role="alert">{error}</p>}

      {segment && (
        <section className="pc-card" aria-labelledby="cp-new">
          <h3 id="cp-new" className="pc-org"><Megaphone size={15} aria-hidden="true" /> New campaign</h3>
          {preview === null ? <p className="pc-muted">Finding customers…</p> : preview.length === 0 ? (
            <p className="pc-muted">Nobody is in this segment right now.</p>
          ) : (
            <>
              <ul className="dk-chips" aria-label="Customers in this segment">
                {preview.map((o) => <li key={o.slug}><Link to={`/platform/organizations/${o.slug}`}>{o.name}</Link>
                  <small className="pc-muted"> · health {o.health}{o.reason ? ` · ${o.reason}` : ''}</small></li>)}
              </ul>
              <form className="sd-update-form" onSubmit={launch}>
                <input aria-label="Task title" required value={form.task_title} onChange={set('task_title')} />
                <select aria-label="Task type" value={form.task_kind} onChange={set('task_kind')}>
                  {TASK_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select aria-label="Owner" value={form.assigned_to} onChange={set('assigned_to')}>
                  <option value="">Unassigned</option>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <input aria-label="Due in days" type="number" min="0" max="90" value={form.due_in_days} onChange={set('due_in_days')} />
                <textarea aria-label="Notes" rows={2} placeholder="Talking points (optional)" value={form.notes} onChange={set('notes')} />
                <div className="pc-actions">
                  <button type="submit" className="btn btn-primary btn-sm">Open {preview.length} outreach task{preview.length === 1 ? '' : 's'}</button>
                </div>
              </form>
            </>
          )}
        </section>
      )}

      {data.campaigns.length === 0 ? (
        <EmptyState variant="cleared" title="No campaigns yet" body="Pick a segment above to reach out to every customer in it." />
      ) : (
        <div className="pc-list">
          {data.campaigns.map((c) => (
            <article key={c.id} className="pc-card cs-task" aria-label={c.name}>
              <div>
                <strong>{c.name}</strong>
                <span className="pc-muted">{c.segment_display} · {c.task_title} · {c.assigned_to_name || 'unassigned'} · started {day(c.created_at)}</span>
              </div>
              <div className="dk-progress" aria-label={`${c.done} of ${c.tasks} done`}>
                <span className="cs-bar" aria-hidden="true"><i style={{ width: `${c.percent}%` }} /></span>
                <small>{c.done}/{c.tasks} done · {c.open} open</small>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
};

export default Campaigns;
