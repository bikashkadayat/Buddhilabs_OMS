import React, { useState } from 'react';
import { UserPlus, Star } from 'lucide-react';

import { platformService } from '../../../services/platformService';
import { describeApiError } from '../../../services/apiErrors';
import { TICKET_CATEGORIES } from '../../../utils/supportFormat';

/**
 * Support teams: who is in each, what each owns, and how loaded they are.
 * A category belongs to exactly one team -- that is how a new ticket finds
 * its owner. Leads hear about escalations; an agent marked away keeps their
 * place but is skipped by auto-assignment.
 */
const CATEGORY = Object.fromEntries(TICKET_CATEGORIES.map(([v, l]) => [v, l]));

const Team = ({ team, agents, onChanged }) => {
  const [add, setAdd] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (fn) => {
    setBusy(true); setError('');
    try { await fn(); await onChanged(); } catch (e) { setError(describeApiError(e, 'That didn’t save.')); }
    finally { setBusy(false); }
  };
  const inTeam = new Set(team.members.map((m) => m.user));

  return (
    <article className="pc-card dk-team" aria-label={team.name}>
      <header className="dk-team-head">
        <div>
          <h3 className="pc-org">{team.name}{team.is_default && <span className="pf-chip"> default</span>}</h3>
          <p className="pc-muted">{team.description}</p>
        </div>
        <p className="dk-team-load"><b>{team.open}</b> open · {team.unassigned} unassigned · <span className={team.overdue ? 'sd-sla is-bad' : ''}>{team.overdue} overdue</span></p>
      </header>
      <p className="dk-line"><span className="pc-muted">Owns:</span> {team.categories.length
        ? team.categories.map((c) => CATEGORY[c] || c).join(' · ') : 'nothing by category'}</p>
      <label className="dk-toggle">
        <input type="checkbox" checked={team.auto_assign} disabled={busy}
               onChange={(e) => act(() => platformService.updateSupportTeam(team.id, { auto_assign: e.target.checked }))} />
        Assign new tickets to the least-loaded available member
      </label>
      {error && <p className="pc-err" role="alert">{error}</p>}
      {team.members.length === 0 ? (
        <p className="pc-muted">Nobody yet — its tickets wait in the team queue.</p>
      ) : (
        <ul className="dk-members">
          {team.members.map((m) => (
            <li key={m.id}>
              <span>{m.role === 'lead' && <Star size={13} aria-label="Lead" />} <strong>{m.name}</strong>
                <small className="pc-muted"> · {m.open_tickets} open{m.is_available ? '' : ' · away'}</small></span>
              <span className="dk-member-actions">
                <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                        onClick={() => act(() => platformService.setTeamMember(team.id, { user: m.user, role: m.role === 'lead' ? 'agent' : 'lead' }))}>
                  {m.role === 'lead' ? 'Make agent' : 'Make lead'}
                </button>
                <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                        onClick={() => act(() => platformService.setTeamMember(team.id, { user: m.user, is_available: !m.is_available }))}>
                  {m.is_available ? 'Mark away' : 'Mark available'}
                </button>
                <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                        onClick={() => act(() => platformService.setTeamMember(team.id, { user: m.user, remove: true }))}>Remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form className="dk-inline" onSubmit={(e) => {
        e.preventDefault();
        act(() => platformService.setTeamMember(team.id, { user: add })).then(() => setAdd(''));
      }}>
        <select aria-label={`Add agent to ${team.name}`} value={add} onChange={(e) => setAdd(e.target.value)}>
          <option value="">Add an agent…</option>
          {agents.filter((a) => !inTeam.has(a.id)).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button type="submit" className="btn btn-ghost btn-sm" disabled={busy || !add}>
          <UserPlus size={14} aria-hidden="true" /> Add
        </button>
      </form>
    </article>
  );
};

const Teams = ({ teams, agents, onChanged }) => {
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  return (
    <div className="dk-teams">
      <div className="cs-grid">
        {teams.map((t) => <Team key={t.id} team={t} agents={agents} onChanged={onChanged} />)}
      </div>
      <form className="sd-filters" onSubmit={async (e) => {
        e.preventDefault(); setError('');
        try { await platformService.createSupportTeam({ name }); setName(''); await onChanged(); }
        catch (err) { setError(describeApiError(err, 'That didn’t save.')); }
      }}>
        <input aria-label="New team name" placeholder="New team, e.g. Enterprise Desk" value={name}
               onChange={(e) => setName(e.target.value)} required />
        <button type="submit" className="btn btn-primary btn-sm">Create team</button>
      </form>
      {error && <p className="pc-err" role="alert">{error}</p>}
      <h3 className="dk-h">Agents</h3>
      <ul className="dk-members">
        {agents.map((a) => (
          <li key={a.id}><span><strong>{a.name}</strong> <small className="pc-muted">@{a.username} · {a.open_tickets} open ·{' '}
            {a.teams?.length ? a.teams.map((x) => `${x.name}${x.role === 'lead' ? ' (lead)' : ''}`).join(', ') : 'no team'}</small></span></li>
        ))}
      </ul>
    </div>
  );
};

export default Teams;
