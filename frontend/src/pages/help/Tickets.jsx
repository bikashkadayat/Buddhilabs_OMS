import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus } from 'lucide-react';

import EmptyState from '../../components/common/EmptyState';
import { useAuth } from '../../hooks/useAuth';
import { supportService } from '../../services/supportService';
import { STATUS_TONE, when } from '../../utils/supportFormat';

/**
 * My tickets: everything this person has asked us, and where each stands.
 * An administrator can widen it to the whole organization -- the person who
 * reported a problem may be on leave when the reply arrives.
 */
const Tickets = () => {
  const { role } = useAuth();
  const [show, setShow] = useState('open');
  const [scope, setScope] = useState('mine');
  const [rows, setRows] = useState(null);

  useEffect(() => {
    supportService.mine({
      ...(show === 'open' ? { open: 1 } : {}),
      ...(scope === 'organization' ? { scope: 'organization' } : {}),
    }).then((data) => setRows(data.filter((r) => r.kind !== 'feature')))
      .catch(() => setRows([]));
  }, [show, scope]);

  return (
    <div className="page hc sc">
      <div className="sc-head">
        <h1 className="hc-h1">My tickets</h1>
        <Link className="btn btn-primary btn-sm" to="/help/contact"><Plus size={14} aria-hidden="true" /> Create ticket</Link>
      </div>
      <div className="sc-toolbar">
        <div className="pf-tabs" role="tablist" aria-label="Show">
          {[['open', 'Open'], ['all', 'All']].map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={show === key}
                    className={`pf-tab${show === key ? ' is-active' : ''}`} onClick={() => { setRows(null); setShow(key); }}>{label}</button>
          ))}
        </div>
        {role === 'admin' && (
          <label className="sc-scope">
            <input type="checkbox" checked={scope === 'organization'}
                   onChange={(e) => { setRows(null); setScope(e.target.checked ? 'organization' : 'mine'); }} />
            {' '}Everyone in my organization
          </label>
        )}
      </div>

      {rows === null ? <p className="hc-muted">Loading…</p> : rows.length === 0 ? (
        <EmptyState
          variant={show === 'open' ? 'cleared' : 'first'}
          title={show === 'open' ? 'No open tickets.' : 'You haven’t opened a ticket yet.'}
          body="If something isn’t working, open a ticket from the page it’s on — we see exactly where you were."
          action={{ to: '/help/contact', label: 'Create a ticket' }} />
      ) : (
        <ul className="sc-list">
          {rows.map((r) => (
            <li key={r.id}>
              <Link to={`/help/tickets/${r.id}`} className={`sc-row${r.unread ? ' is-unread' : ''}`}>
                <span className="sc-ref">{r.reference}</span>
                <span className="sc-title">
                  {r.unread && <span className="sc-dot" aria-label="New reply" />}
                  <strong>{r.subject || r.message.slice(0, 70)}</strong>
                  <small className="hc-muted">
                    {r.category_display} · opened {when(r.created_at)}
                    {scope === 'organization' && ` · ${r.submitted_by_name}`}
                  </small>
                </span>
                <span className={`pf-chip ${STATUS_TONE[r.status] || ''}`}>{r.status_display}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default Tickets;
