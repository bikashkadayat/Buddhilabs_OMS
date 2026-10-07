import React from 'react';
import { BadgeCheck, CalendarCheck2, CalendarClock, CalendarDays, ClipboardCheck, HousePlus, UserCheck, UsersRound } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import ModuleLauncher from '../../components/layout/ModuleLauncher';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';
import { useCorrectionCounts } from '../../hooks/useWorkforce';
import { attendanceService } from '../../services/attendanceService';
import { leaveService } from '../../services/leaveService';
import { appraisalService } from '../../services/appraisalService';

/**
 * People & Attendance launcher (Phase E).
 *
 * Replaces five old sidebar sections that described one subject from different
 * angles. The Team card is gated on workforceTeam - the same check the old rail
 * used - so an Employee simply does not see it.
 */
const PeopleLauncher = () => {
  const { role } = useAuth();
  const opts = { staleTime: 60_000, retry: false };
  const { data: today } = useQuery({ queryKey: ['attendance', 'today'], queryFn: attendanceService.today, ...opts });
  const { data: balances } = useQuery({ queryKey: ['leaves', 'balances'], queryFn: leaveService.getBalances, ...opts });
  const { data: corrections } = useCorrectionCounts();
  // Shares the cache key the rail badge and the Home card already use, so
  // these three cards cost no extra request.
  const { data: appraisal } = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard, ...opts,
  });

  const rows = balances?.data ?? [];
  const annual = rows.find((b) => /annual/i.test(b.leave_type || ''));
  const correctionTotal = (corrections?.manager_stage || 0)
    + (corrections?.hr_stage || 0) + (corrections?.mine_open || 0);

  const cards = [
    {
      key: 'attendance', title: 'Attendance', icon: CalendarCheck2, accent: '#274095', to: '/my-attendance',
      counts: [['today', today?.status ? today.status.replace(/_/g, ' ') : '—']],
      action: { label: 'My attendance', to: '/my-attendance' },
    },
    {
      key: 'leave', title: 'Leave', icon: CalendarDays, accent: '#126b4e', to: '/leave',
      counts: [['days annual left', annual?.remaining]],
      action: can(role, 'applyLeave') ? { label: 'Apply for leave', to: '/leave/apply' } : null,
    },
    {
      key: 'team', title: 'Team', icon: UsersRound, accent: '#3c4a6e', to: '/workforce/team',
      blurb: 'Attendance and leave across your department',
      gate: 'workforceTeam',
    },
    {
      key: 'corrections', title: 'Corrections', icon: BadgeCheck, accent: '#a35b06', to: '/workforce/corrections',
      counts: [['punch corrections open', correctionTotal || 0]],
    },
    {
      key: 'wfh', title: 'WFH & comp-off', icon: HousePlus, accent: '#0f7f8b', to: '/workforce/wfh',
      blurb: 'Work from home and compensatory time',
    },
    // Phase APM-03b. This card shipped as a disabled placeholder pointing at
    // /people; it now leads somewhere. It is also the module's main way in —
    // the workspace rail is full at nine links and the People rail at seven, so
    // Appraisal takes the same route the HR command centre and conflicts take:
    // an overview tile, recorded in navConfig's UNLISTED, indexed for search.
    {
      key: 'appraisal', title: 'Appraisal', icon: ClipboardCheck, accent: '#5b4b8a', to: '/appraisals',
      counts: [['my stage', appraisal?.employee?.stage || 'none open']],
      action: { label: 'My appraisal', to: '/appraisals' },
    },
    // Shown only to somebody who actually supervises: the endpoint correctly
    // returns nothing for everybody else, and a card that is always empty
    // teaches people the launcher is unreliable.
    ...(appraisal?.manager ? [{
      key: 'appraisal-team', title: 'Team reviews', icon: UserCheck, accent: '#3c4a6e', to: '/appraisals/team',
      counts: [['appraisals with you', appraisal.manager.pending_reviews || 0]],
    }] : []),
    ...(appraisal?.hr ? [{
      key: 'appraisal-hr', title: 'Appraisal cycle', icon: CalendarClock, accent: '#7c2d12', to: '/appraisals/hr',
      counts: [['cycle complete', `${appraisal.hr.cycle_completion?.percent ?? 0}%`]],
      action: { label: 'Cycles', to: '/appraisals/cycles' },
    }] : []),
  ].filter((c) => !c.gate || can(role, c.gate));

  return (
    <ModuleLauncher
      title="People & Attendance"
      description="You, your team and your time."
      cards={cards}
    />
  );
};

export default PeopleLauncher;
