import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Users } from 'lucide-react';

import { workforceService } from '../../services/workforceService';

/**
 * A manager's team, today, on their Home.
 *
 * ONE MANAGER VIEW. Department heads had a team dashboard, attendance
 * records, a team task board and the leave queue -- four places to find out
 * whether their people turned up. Home now answers that at a glance, under
 * their own attendance, and links to the one team dashboard for the detail.
 * Approvals waiting on them are already Home's "Pending reviews".
 */
const CHIPS = [
  ['present', 'present', 'is-good'],
  ['late', 'late', 'is-warn'],
  ['half_day', 'half day', 'is-warn'],
  ['on_leave', 'on leave', 'is-info'],
  ['absent', 'absent', 'is-bad'],
];

const TeamToday = () => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['workforce', 'team', 'home'],
    queryFn: () => workforceService.team(),
    staleTime: 60_000,
    retry: false,
  });

  if (isError) return null;
  const counts = data?.counts || {};
  const absent = data?.absent_employees || [];

  return (
    <section className="tt" aria-labelledby="tt-h" data-tour="team">
      <div className="tt-head">
        <h2 id="tt-h"><Users size={18} aria-hidden="true" /> Your team today</h2>
        <Link to="/workforce/team" className="hm-sec-link">Team dashboard →</Link>
      </div>
      {isLoading ? (
        <p className="tt-quiet">Checking who’s in…</p>
      ) : data?.is_holiday ? (
        <p className="tt-quiet">It’s a holiday{data.holiday_name ? ` — ${data.holiday_name}` : ''}.</p>
      ) : (
        <>
          <div className="tt-chips">
            {CHIPS.filter(([key]) => counts[key]).map(([key, label, tone]) => (
              <span key={key} className={`tt-chip ${tone}`}>
                <b>{counts[key]}</b> {label}
              </span>
            ))}
            {!CHIPS.some(([key]) => counts[key]) && (
              <span className="tt-quiet">No attendance recorded yet today.</span>
            )}
          </div>
          {absent.length > 0 && (
            <p className="tt-absent">
              Not in yet: {absent.slice(0, 4).map((p) => p.name).join(', ')}
              {absent.length > 4 ? ` and ${absent.length - 4} more` : ''}
            </p>
          )}
        </>
      )}
    </section>
  );
};

export default TeamToday;
