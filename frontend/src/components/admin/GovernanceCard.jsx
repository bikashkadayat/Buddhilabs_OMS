import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ShieldAlert, ShieldCheck } from 'lucide-react';

import { leaveService } from '../../services/leaveService';

/**
 * Department ownership, on the admin dashboard
 * (Phase DASHBOARD-GOVERNANCE-CLEANUP).
 *
 * The DETAIL that Home no longer carries. Home says "Governance Issues (4)";
 * this says which four, and links to the page where they are fixed - because
 * this is a page somebody opens to administer the organisation, where an
 * organisation-wide gap is exactly on topic.
 *
 * It renders when everything is in order too, unlike the old banner. A panel
 * that appears only when something is wrong leaves a reader unsure whether the
 * check ran at all, and "every department has a head" is worth being able to
 * confirm.
 *
 * Same endpoint as System Health and the Department Ownership report
 * (leaves.governance), so the three cannot disagree about the same number.
 */
const GovernanceCard = () => {
  const { data, isError } = useQuery({
    queryKey: ['departments', 'governance'],
    queryFn: leaveService.departmentGovernance,
    staleTime: 10 * 60_000,
    retry: false,
  });

  if (isError || !data) return null;
  const missing = data.missing_head_names || [];
  const healthy = data.status !== 'critical';

  return (
    <div className="table-card ds-card" style={{ padding: 24, marginBottom: 32 }}>
      <div className="ds-head">
        {healthy
          ? <ShieldCheck size={20} color="var(--success)" />
          : <ShieldAlert size={20} color="var(--danger)" />}
        <h3>Department Governance</h3>
      </div>

      <p className="task-sub" style={{ margin: '4px 0 0' }}>
        {healthy
          ? `All ${data.active} active departments have a Department Head.`
          : `${missing.length} of ${data.active} active departments have nobody `
            + 'recorded as Department Head.'}
      </p>

      {!healthy && (
        <ul className="gov-card-list">
          {missing.map((name) => (
            <li key={name}>
              <span className="gov-gap">No head</span>
              <span>{name}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="task-sub" style={{ marginTop: 10 }}>
        <Link to="/admin/leaves/departments">Departments</Link>
        {' · '}
        <Link to="/monitoring">System Health</Link>
        {' · '}
        <Link to="/reports/build/department_ownership">Ownership report</Link>
      </p>
    </div>
  );
};

export default GovernanceCard;
