import React from 'react';
import { BarChart3, Boxes, CalendarCheck2, CalendarDays, TrendingUp, UsersRound } from 'lucide-react';
import ModuleLauncher from '../../components/layout/ModuleLauncher';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';

/**
 * Reports launcher (Phase E).
 *
 * Mounted at /reports/overview rather than /reports, because /reports is the
 * existing ReportsHub and replacing it would delete a page. Nothing is removed:
 * "Build a report" links straight to it.
 *
 * The nine analytics dashboards become six described cards instead of nine
 * near-identical sidebar rows. Gates are the same analyticsView / analyticsOrg
 * checks the old rail used.
 */
const ReportsLauncher = () => {
  const { role } = useAuth();

  const cards = [
    {
      key: 'exec', title: 'Executive', icon: TrendingUp, accent: '#1c2540', to: '/analytics/executive',
      blurb: 'Organisation-wide overview', gate: 'analyticsOrg',
    },
    {
      key: 'hr', title: 'HR', icon: UsersRound, accent: '#4c3a9e', to: '/analytics/hr',
      blurb: 'Headcount, turnover and leave KPIs', gate: 'analyticsOrg',
    },
    {
      key: 'attendance', title: 'Attendance', icon: CalendarCheck2, accent: '#274095', to: '/analytics/attendance',
      blurb: 'Trends, punctuality and device health', gate: 'analyticsView',
    },
    {
      key: 'leave', title: 'Leave', icon: CalendarDays, accent: '#126b4e', to: '/analytics/leave',
      blurb: 'Usage by type and department', gate: 'analyticsView',
    },
    {
      key: 'inventory', title: 'Inventory', icon: Boxes, accent: '#0f7f8b', to: '/inventory/reports',
      blurb: 'Stock, assignment and audit reports',
    },
    {
      key: 'build', title: 'Build a report', icon: BarChart3, accent: '#a35b06', to: '/reports',
      blurb: 'Generate and download', action: { label: 'History', to: '/reports/history' },
    },
  ].filter((c) => !c.gate || can(role, c.gate));

  return (
    <ModuleLauncher
      title="Reports"
      description="Aggregate views. Reports describe departments and trends, never individuals."
      cards={cards}
    />
  );
};

export default ReportsLauncher;
