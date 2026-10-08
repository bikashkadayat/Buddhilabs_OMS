import React, { useCallback, useEffect, useState } from 'react';

import PageHeader from '../../components/common/PageHeader';
import { platformService } from '../../services/platformService';
import { describeApiError } from '../../services/apiErrors';
import { when } from '../../utils/supportFormat';

/**
 * Two things customers read and the platform team writes: What's New, and
 * incident / maintenance notices on System Status. Drafts stay private until
 * published; a notice is live until it is resolved.
 */
const CATEGORIES = [['new', 'New'], ['improved', 'Improved'], ['fixed', 'Fixed'], ['release', 'Release notes'],
  ['announcement', 'Announcement'], ['maintenance', 'Maintenance']];
const COMPONENTS = [['platform', 'Platform'], ['email', 'Email delivery'], ['storage', 'File storage'],
  ['payments', 'Payments'], ['domains', 'Domain verification'], ['attendance', 'Attendance services']];
const LEVELS = [['degraded', 'Degraded'], ['outage', 'Outage'], ['maintenance', 'Maintenance']];
const BLANK = { title: '', summary: '', body: '', category: 'new', link: '' };

const UpdatesStatus = () => {
  const [updates, setUpdates] = useState([]);
  const [notices, setNotices] = useState([]);
  const [draft, setDraft] = useState(BLANK);
  const [notice, setNotice] = useState({ component: 'platform', level: 'degraded', message: '' });
  const [error, setError] = useState('');

  const load = useCallback(() => Promise.all([
    platformService.productUpdates().then(({ data }) => setUpdates(data)),
    platformService.statusNotices().then(({ data }) => setNotices(data)),
  ]).catch(() => setError('Could not load.')), []);
  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setError('');
    try { await fn(); await load(); } catch (e) {
      const d = e?.response?.data || {};
      setError(d.link?.[0] || d.title?.[0] || d.message?.[0] || describeApiError(e, 'That didn’t save.'));
    }
  };
  const set = (setter, key) => (e) => setter((v) => ({ ...v, [key]: e.target.value }));

  return (
    <div className="page">
      <PageHeader breadcrumb="Platform" title="Updates & status"
                  description="What customers see under Help & Support → Product updates and System status." />
      {error && <p className="pc-err" role="alert">{error}</p>}

      <section className="pc-card" aria-labelledby="us-status">
        <h2 id="us-status" className="pc-org">Status notices</h2>
        <p className="pc-muted">Automatic checks run on their own. Post a notice when you know more than they do.</p>
        <form className="sd-filters" onSubmit={(e) => { e.preventDefault(); run(async () => {
          await platformService.createStatusNotice(notice); setNotice((n) => ({ ...n, message: '' }));
        }); }}>
          <select aria-label="Component" value={notice.component} onChange={set(setNotice, 'component')}>
            {COMPONENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Level" value={notice.level} onChange={set(setNotice, 'level')}>
            {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <input aria-label="Message" required placeholder="What’s happening, and when the next update is"
                 value={notice.message} onChange={set(setNotice, 'message')} />
          <button type="submit" className="btn btn-primary btn-sm">Post notice</button>
        </form>
        <ul className="sc-notices">
          {notices.map((n) => (
            <li key={n.id}>
              <strong>{n.component_display} · {n.level}</strong> — {n.message}
              <span className="pc-muted"> · {when(n.starts_at)}{n.resolved_at ? ` · resolved ${when(n.resolved_at)}` : ''}</span>
              {!n.resolved_at && (
                <button type="button" className="btn btn-ghost btn-xs"
                        onClick={() => run(() => platformService.resolveStatusNotice(n.id))}>Resolve</button>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="pc-card" aria-labelledby="us-updates">
        <h2 id="us-updates" className="pc-org">What’s new</h2>
        <form className="sd-update-form" onSubmit={(e) => { e.preventDefault(); run(async () => {
          await platformService.createProductUpdate(draft); setDraft(BLANK);
        }); }}>
          <input aria-label="Title" required placeholder="e.g. New Attendance System" value={draft.title} onChange={set(setDraft, 'title')} />
          <select aria-label="Category" value={draft.category} onChange={set(setDraft, 'category')}>
            {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <input aria-label="Summary" placeholder="One line customers see in the list" value={draft.summary} onChange={set(setDraft, 'summary')} />
          <textarea aria-label="Details" rows={3} placeholder="Details (optional)" value={draft.body} onChange={set(setDraft, 'body')} />
          <input aria-label="Link" placeholder="Link inside the product, e.g. /settings/attendance" value={draft.link} onChange={set(setDraft, 'link')} />
          <button type="submit" className="btn btn-primary btn-sm">Save draft</button>
        </form>
        <ul className="sc-notices">
          {updates.map((u) => (
            <li key={u.id}>
              <strong>{u.title}</strong> <span className="pc-muted">· {u.category_display} · {u.published_at ? `published ${when(u.published_at)}` : 'draft'}</span>
              <button type="button" className="btn btn-ghost btn-xs"
                      onClick={() => run(() => platformService.updateProductUpdate(u.id, { publish: !u.published_at }))}>
                {u.published_at ? 'Unpublish' : 'Publish'}
              </button>
              <button type="button" className="btn btn-ghost btn-xs"
                      onClick={() => run(() => platformService.deleteProductUpdate(u.id))}>Delete</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
};

export default UpdatesStatus;
