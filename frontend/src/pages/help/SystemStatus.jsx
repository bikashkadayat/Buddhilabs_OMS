import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, AlertTriangle, XCircle, Wrench, RefreshCw } from 'lucide-react';

import { supportService } from '../../services/supportService';
import { when } from '../../utils/supportFormat';

/**
 * Is it us or is it you? Each part of the platform with its state, plus any
 * notice the platform team has posted. If everything is green, the problem
 * is likely local -- and the page says how to report it.
 */
const STATE = {
  operational: { label: 'Operational', Icon: CheckCircle2, tone: 'is-ok' },
  maintenance: { label: 'Maintenance', Icon: Wrench, tone: 'is-warn' },
  degraded: { label: 'Degraded', Icon: AlertTriangle, tone: 'is-warn' },
  outage: { label: 'Outage', Icon: XCircle, tone: 'is-bad' },
};

const SystemStatus = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const fetchStatus = () => supportService.status()
    .then((d) => { setData(d); setError(false); }).catch(() => setError(true));
  useEffect(() => { fetchStatus(); }, []);
  const load = () => { setError(false); fetchStatus(); };

  const overall = data ? STATE[data.overall] : null;
  return (
    <div className="page hc sc">
      <div className="sc-head">
        <h1 className="hc-h1">System status</h1>
        <button type="button" className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
      </div>
      {error && <p className="acc-err" role="alert">Status could not be loaded. If the whole workspace is slow, it may be on our side — please try again in a minute.</p>}
      {data && (
        <>
          <p className={`sc-overall ${overall.tone}`} role="status">
            <overall.Icon size={20} aria-hidden="true" /> {data.summary}
          </p>
          {data.notices.length > 0 && (
            <ul className="sc-notices">
              {data.notices.map((n) => (
                <li key={n.id}><strong>{n.component_display}:</strong> {n.message} <span className="hc-muted">· since {when(n.starts_at)}</span></li>
              ))}
            </ul>
          )}
          <ul className="sc-components">
            {data.components.map((c) => {
              const s = STATE[c.state] || STATE.degraded;
              return (
                <li key={c.key} className={s.tone}>
                  <span>{c.label}</span>
                  <span className="sc-state"><s.Icon size={15} aria-hidden="true" /> {s.label}</span>
                  {c.note && <small className="hc-muted">{c.note}</small>}
                </li>
              );
            })}
          </ul>
          <p className="hc-muted">Checked {when(data.checked_at)}.
            {data.overall === 'operational' && <> Everything is working on our side — if something isn’t working for you, <Link to="/help/contact">open a ticket</Link> and we’ll look.</>}
          </p>
        </>
      )}
    </div>
  );
};

export default SystemStatus;
