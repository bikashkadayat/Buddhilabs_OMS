import React, { useEffect, useState } from 'react';
import { AlertTriangle, Clock, Hourglass, Users, CheckCircle2, Flame, UserX } from 'lucide-react';

import { platformService } from '../../../services/platformService';

/**
 * The SLA board: what is about to go wrong, what already has, and where each
 * ticket is waiting. Critical due = a critical ticket inside its last two
 * hours; overdue = past SLA with the clock running; waiting on the team =
 * open or in progress (the ball is ours); waiting on the customer = clock
 * paused. Click a ticket to open it on the desk.
 */
const COLUMNS = [
  ['critical_due', 'Critical due', Flame, 'is-bad'],
  ['overdue', 'Overdue', AlertTriangle, 'is-bad'],
  ['escalated', 'Escalated', AlertTriangle, 'is-warn'],
  ['waiting_team', 'Waiting on team', Users, ''],
  ['waiting_customer', 'Waiting on customer', Hourglass, ''],
  ['unassigned', 'Unassigned', UserX, 'is-warn'],
  ['resolved_today', 'Resolved today', CheckCircle2, 'is-good'],
];

const left = (t) => {
  if (t.minutes_left == null) return null;
  const m = t.minutes_left;
  if (m < 0) return `${Math.round(-m / 60)}h over`;
  return m < 120 ? `${m}m left` : `${Math.round(m / 60)}h left`;
};

const SlaBoard = ({ teams, onOpen }) => {
  const [team, setTeam] = useState('');
  const [board, setBoard] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    platformService.supportSla(team).then(({ data }) => setBoard(data))
      .catch(() => setError('The SLA board couldn’t be loaded.'));
  }, [team]);

  if (error) return <p className="pc-err" role="alert">{error}</p>;
  if (!board) return <p className="pc-muted">Loading…</p>;
  const met = board.sla_met_30d || {};
  return (
    <div className="dk-board">
      <div className="sd-filters">
        <select aria-label="Team" value={team} onChange={(e) => { setBoard(null); setTeam(e.target.value); }}>
          <option value="">All teams</option>
          {teams.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        <span className="pc-muted dk-meta">
          <Clock size={13} aria-hidden="true" /> SLA met {met.percent ?? '—'}% over 30 days ({met.on_time ?? 0}/{met.resolved ?? 0})
          {board.rules?.length > 0 && ` · auto-escalation: ${board.rules.map((r) => `${r.priority} after ${r.hours}h`).join(', ')}`}
        </span>
      </div>
      <div className="sd-tiles" aria-label="SLA summary">
        {COLUMNS.map(([key, label, , tone]) => (
          <div key={key} className={`sd-tile${board[key]?.count && tone ? ` ${tone}` : ''}`}>
            <strong>{board[key]?.count ?? 0}</strong><span>{label}</span>
          </div>
        ))}
      </div>
      <div className="dk-columns">
        {COLUMNS.map(([key, label, Icon]) => (
          <section key={key} className="dk-col" aria-labelledby={`sla-${key}`}>
            <h3 id={`sla-${key}`} className="dk-h"><Icon size={14} aria-hidden="true" /> {label} <span className="pc-muted">({board[key]?.count ?? 0})</span></h3>
            {(board[key]?.tickets || []).length === 0 ? <p className="pc-muted">None.</p> : (
              <ul className="dk-cards">
                {board[key].tickets.map((t) => (
                  <li key={t.id}>
                    <button type="button" className="dk-card" onClick={() => onOpen(t.id)}>
                      <span className={`sd-prio is-${t.priority}`} aria-hidden="true" />
                      <span className="dk-card-main">
                        <strong>{t.reference} · {t.subject || 'No subject'}</strong>
                        <small className="pc-muted">{t.organization || 'platform'} · {t.team || 'no team'} · {t.assigned_to || 'unassigned'}
                          {t.escalation_level ? ` · L${t.escalation_level}` : ''}</small>
                      </span>
                      {key !== 'resolved_today' && left(t) && <small className={`sd-sla${t.minutes_left < 0 ? ' is-bad' : t.minutes_left < 120 ? ' is-warn' : ''}`}>{left(t)}</small>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
      {board.by_team?.length > 0 && (
        <div className="dk-table-wrap"><table className="lr-table dk-team-table">
          <caption className="pc-muted">Open work by team</caption>
          <thead><tr><th scope="col">Team</th><th scope="col">Open</th><th scope="col">Overdue</th><th scope="col">Unassigned</th></tr></thead>
          <tbody>
            {board.by_team.map((x) => (
              <tr key={x.id}><th scope="row">{x.name}</th><td>{x.open}</td><td>{x.overdue}</td><td>{x.unassigned}</td></tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
};

export default SlaBoard;
