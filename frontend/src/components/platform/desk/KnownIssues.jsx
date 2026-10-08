import React, { useState } from 'react';

import { platformService } from '../../../services/platformService';
import { describeApiError } from '../../../services/apiErrors';
import EmptyState from '../../common/EmptyState';
import { TICKET_CATEGORIES, when } from '../../../utils/supportFormat';

/**
 * Known issues: a problem written up once. The assistant offers the
 * workaround to any customer who describes the same symptoms (public issues
 * with a workaround only), every ticket it caused is linked to it, and
 * marking it fixed can tell all of those customers at once.
 */
const STATES = [['investigating', 'Investigating'], ['workaround', 'Workaround available'], ['fixed', 'Fixed']];
const BLANK = { title: '', symptoms: '', workaround: '', category: '', state: 'investigating', is_public: true };

const Issue = ({ issue, onChanged }) => {
  const [edit, setEdit] = useState(null);
  const [error, setError] = useState('');
  const save = async (body) => {
    setError('');
    try { await platformService.updateKnownIssue(issue.id, body); setEdit(null); await onChanged(); }
    catch (e) { setError(describeApiError(e, 'That didn’t save.')); }
  };
  return (
    <article className="pc-card dk-issue" aria-label={issue.title}>
      <header className="dk-team-head">
        <div>
          <h3 className="pc-org">{issue.title}</h3>
          <p className="pc-muted">{issue.state_display}{issue.is_public ? '' : ' · internal only'} · {issue.tickets} ticket{issue.tickets === 1 ? '' : 's'} from {issue.organizations} customer{issue.organizations === 1 ? '' : 's'} · {issue.open_tickets} open · answered {issue.deflected} before a ticket · updated {when(issue.updated_at)}</p>
        </div>
      </header>
      {edit ? (
        <form className="sd-update-form" onSubmit={(e) => { e.preventDefault(); save(edit); }}>
          <input aria-label="Title" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
          <select aria-label="State" value={edit.state} onChange={(e) => setEdit({ ...edit, state: e.target.value })}>
            {STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <textarea aria-label="Symptoms" rows={2} placeholder="Words customers use to describe it" value={edit.symptoms}
                    onChange={(e) => setEdit({ ...edit, symptoms: e.target.value })} />
          <textarea aria-label="Workaround" rows={3} placeholder="What to do meanwhile — customers see this" value={edit.workaround}
                    onChange={(e) => setEdit({ ...edit, workaround: e.target.value })} />
          <div className="pc-actions">
            <button type="submit" className="btn btn-primary btn-sm">Save</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEdit(null)}>Cancel</button>
          </div>
        </form>
      ) : (
        <>
          {issue.symptoms && <p className="dk-line"><span className="pc-muted">Symptoms:</span> {issue.symptoms}</p>}
          <p className="dk-line"><span className="pc-muted">Workaround:</span> {issue.workaround || 'none yet — not offered to customers'}</p>
          <div className="pc-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEdit({
              title: issue.title, state: issue.state, symptoms: issue.symptoms, workaround: issue.workaround })}>Edit</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => save({ is_public: !issue.is_public })}>
              {issue.is_public ? 'Hide from customers' : 'Offer to customers'}
            </button>
            {issue.state !== 'fixed' && (
              <button type="button" className="btn btn-primary btn-sm" onClick={() => save({ state: 'fixed', resolve_linked: true })}>
                Mark fixed{issue.open_tickets ? ` & resolve ${issue.open_tickets} ticket${issue.open_tickets === 1 ? '' : 's'}` : ''}
              </button>
            )}
          </div>
        </>
      )}
      {error && <p className="pc-err" role="alert">{error}</p>}
    </article>
  );
};

const KnownIssues = ({ issues, onChanged }) => {
  const [draft, setDraft] = useState(BLANK);
  const [error, setError] = useState('');
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  return (
    <div className="dk-issues">
      <form className="sd-update-form pc-card" onSubmit={async (e) => {
        e.preventDefault(); setError('');
        try { await platformService.createKnownIssue(draft); setDraft(BLANK); await onChanged(); }
        catch (err) { setError(describeApiError(err, 'That didn’t save.')); }
      }}>
        <input aria-label="Title" required placeholder="e.g. Late marks off by one hour after the time change" value={draft.title} onChange={set('title')} />
        <select aria-label="Category" value={draft.category} onChange={set('category')}>
          <option value="">Any category</option>
          {TICKET_CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <textarea aria-label="Symptoms" rows={2} placeholder="Words customers use to describe it" value={draft.symptoms} onChange={set('symptoms')} />
        <textarea aria-label="Workaround" rows={2} placeholder="What to do meanwhile — offered to customers once filled in" value={draft.workaround} onChange={set('workaround')} />
        <div className="pc-actions"><button type="submit" className="btn btn-primary btn-sm">Add known issue</button></div>
      </form>
      {error && <p className="pc-err" role="alert">{error}</p>}
      {issues.length === 0 ? (
        <EmptyState variant="cleared" title="No known issues" body="Write one up from a ticket, or here, when several customers hit the same problem." />
      ) : (
        <div className="pc-list">{issues.map((i) => <Issue key={i.id} issue={i} onChanged={onChanged} />)}</div>
      )}
    </div>
  );
};

export default KnownIssues;
