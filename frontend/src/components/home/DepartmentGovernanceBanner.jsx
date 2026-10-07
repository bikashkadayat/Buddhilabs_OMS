import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';
import { leaveService } from '../../services/leaveService';

/**
 * A quiet pointer to organisation-wide governance issues
 * (Phase DASHBOARD-GOVERNANCE-CLEANUP).
 *
 * WHY THIS IS NO LONGER A CRITICAL BANNER
 * ---------------------------------------
 * It used to be a red, full-width alert naming every department with no head,
 * because at the time that gap BLOCKED work: departmental tasks were refused
 * outright. TASK-SIMPLIFICATION removed that dependency - a missing head now
 * costs a pre-filled default and nothing else - so the warning outlived the
 * emergency it was written for.
 *
 * Home is for what blocks the person reading it. An organisation-wide
 * configuration gap is real, worth fixing, and not that. Rendering it as a
 * critical error next to their actual work taught people to scroll past red,
 * which is the one thing a critical banner must never do.
 *
 * WHAT IS KEPT
 * ------------
 * The count, and a way in. The detail lives where somebody goes to act on it:
 * System Health, the Department Ownership report, and the Departments page.
 * Nothing about monitoring, alerting or reporting changed.
 */
const DepartmentGovernanceBanner = () => {
  const { role } = useAuth();
  // Only the people who can act on it: an Admin sets department heads, and HR
  // carries what the gap creates. Nobody else is shown a count they cannot
  // change.
  const isAdmin = can(role, 'userManagement');
  const mayAct = isAdmin || role === 'approver';

  const { data } = useQuery({
    queryKey: ['departments', 'governance'],
    queryFn: leaveService.departmentGovernance,
    enabled: mayAct,
    staleTime: 10 * 60_000,
    retry: false,
  });

  if (!mayAct || !data || data.status !== 'critical') return null;

  // Admins fix it on the Departments page; HR reads it on the health board,
  // which is the governance surface they can reach.
  const to = isAdmin ? '/admin/leaves/departments' : '/monitoring';

  return (
    <p className="hm-gov-note" role="status">
      <ShieldAlert size={16} strokeWidth={1.75} aria-hidden="true" />
      <span>Governance Issues ({data.missing_head})</span>
      <Link to={to} className="hm-gov-link">View details →</Link>
    </p>
  );
};

export default DepartmentGovernanceBanner;
