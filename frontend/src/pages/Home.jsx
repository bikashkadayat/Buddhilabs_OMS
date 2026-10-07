import React, { useMemo } from 'react';
import { useBranding } from '../hooks/useBranding';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../hooks/useAuth';
import { useWorkQueue } from '../hooks/useWorkQueue';
import { taskService } from '../services/taskService';
import { todayBS } from '../services/bsDate';
import { isOverdue, TYPES } from '../services/workQueue';
import { useIsMobile } from '../hooks/useIsMobile';
import StatusStrip from '../components/home/StatusStrip';
import DepartmentGovernanceBanner from '../components/home/DepartmentGovernanceBanner';
import SummaryCards from '../components/home/SummaryCards';
import MyDay from '../components/home/MyDay';
import QuickActions from '../components/home/QuickActions';
import UnreadNotices from '../components/home/UnreadNotices';
import ActivityFeed from '../components/home/ActivityFeed';
import TaskWidgets from '../components/home/TaskWidgets';
import { focusOrder } from '../components/home/focus';
import OnboardingWizard from '../components/onboarding/OnboardingWizard';
import AttendanceHero from '../components/home/AttendanceHero';
import TeamToday from '../components/home/TeamToday';
import RateThis from '../components/help/RateThis';
import { can } from '../services/roles';

/**
 * Home (Phase 203 / blueprint §03, redesigned).
 *
 * The system's front door, owned by no module. The order is the same at every
 * width: greeting and summary, then what to do (focus, shortcuts), then what
 * to know (tasks, updates, today at a glance). On a desktop the "know" band
 * is an aside beside the "do" band; on a phone it follows it.
 *
 * NOTE: no polling loop here. The Leave dashboard runs useAutoRefresh at 20s;
 * carrying that onto a nine-widget page on top of react-query's own intervals
 * would turn the busiest screen in the system into a request storm.
 */

const greeting = (d = new Date()) => {
  const h = d.getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
};

const firstName = (user) => {
  const full = user?.full_name || user?.name || user?.username || '';
  return String(full).trim().split(/\s+/)[0] || 'there';
};

const pad = (n) => String(n).padStart(2, '0');

/** Local date parts — toISOString() is UTC and flips the date after 17:45 NPT. */
const localISODate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const Home = () => {
  const { welcome } = useBranding();
  const { user, role, loading: authLoading } = useAuth();
  const isMobile = useIsMobile();

  const {
    allItems, counts, sources, isLoading, act, retrySources,
  } = useWorkQueue({ enabled: !authLoading });

  // The same key TaskWidgets reads, so this costs no extra request: the two
  // share one entry in the cache.
  const { data: dashboard } = useQuery({
    queryKey: ['tasks', 'dashboard'],
    queryFn: taskService.getDashboard,
    staleTime: 60_000,
    retry: false,
    enabled: Boolean(user),
  });

  const outstanding = useMemo(
    () => allItems.filter((i) => !i.resolved), [allItems],
  );
  // Three on a phone, five on a desktop, most urgent first.
  const focus = useMemo(
    () => focusOrder(outstanding).slice(0, isMobile ? 3 : 5), [outstanding, isMobile],
  );

  const overdueItems = useMemo(() => outstanding.filter((i) => isOverdue(i)), [outstanding]);
  const overdueNoun = overdueItems.every((i) => i.type === TYPES.TASK) ? 'task' : 'item';
  const reviews = (counts.review ?? 0) + (counts.approval ?? 0);
  const toAccept = dashboard?.to_accept ?? 0;

  const urgency = [
    counts.overdue > 0 && {
      key: 'overdue', late: true,
      text: `${plural(counts.overdue, overdueNoun)} ${counts.overdue === 1 ? 'is' : 'are'} overdue.`,
    },
    reviews > 0 && {
      key: 'reviews',
      text: `${plural(reviews, 'review')} ${reviews === 1 ? 'is' : 'are'} waiting for you.`,
    },
    toAccept > 0 && {
      key: 'accept',
      text: `${plural(toAccept, 'task')} ready to start.`,
    },
  ].filter(Boolean);

  const today = new Date();
  const bs = todayBS();
  const dateLine = [
    today.toLocaleDateString(undefined, { weekday: 'long' }),
    bs ? `B.S. ${bs}` : null,
    localISODate(today),
  ].filter(Boolean).join(' · ');

  const degraded = sources.some((s) => !s.ok);
  const joined = user?.date_joined ? new Date(user.date_joined) : null;
  const usedForAWeek = Boolean(joined) && (today - joined) > 7 * 86_400_000;
  const quarter = `${today.getFullYear()}-Q${Math.floor(today.getMonth() / 3) + 1}`;

  return (
    <div className="page hm-page">
      {/* Phase S7 Parts 6-8. ABOVE the hero, and it renders nothing at all
          unless the server says to show it -- which it stops doing once the
          checklist is finished or the administrator dismisses it. A workspace
          in its second month sees no trace of this. */}
      <OnboardingWizard />
      {/* Attendance first: it is what most people opened the system for.
          The greeting lives here too, so the page starts with one block
          about the person and their day rather than two. */}
      <AttendanceHero
        greeting={greeting(today)}
        name={`${firstName(user)}.`}
        welcome={welcome}
        dateLine={dateLine}
      />
      {/* Managers: their team, right under their own day. */}
      {can(role, 'workforceTeam') && <TeamToday />}
      <div className="hm-hero">
        <header className="hm-hd">
          <p className="hm-lead">
            {isLoading
              ? 'Checking what needs your attention…'
              : counts.total === 0
                ? 'Nothing needs your attention today.'
                : (
                  <>
                    You have <strong>{plural(counts.total, 'item')}</strong> that
                    {counts.total === 1 ? ' needs' : ' need'} attention today.
                  </>
                )}
          </p>
          {!isLoading && urgency.length > 0 && (
            <ul className="hm-urgent">
              {urgency.map((u) => (
                <li key={u.key} className={u.late ? 'is-late' : ''}>{u.text}</li>
              ))}
            </ul>
          )}
        </header>

        <SummaryCards counts={counts} dashboard={dashboard} loading={isLoading} />
      </div>

      {/* A department with nobody answerable for it: leave routing falls back
          to HR and department reporting has no owner. It blocks nothing, so it
          is a quiet line rather than a banner. Renders nothing for people who
          cannot act on it, and nothing when there is no gap. */}
      <DepartmentGovernanceBanner />

      <div className="hm-cols">
        <div className="hm-main">
          <MyDay
            items={focus}
            total={counts.total}
            loading={isLoading}
            onAction={act}
          />

          {degraded && (
            <p className="hm-quiet hm-degraded" role="status">
              Some sources could not be loaded, so this may be incomplete.{' '}
              <button type="button" className="wq-link" onClick={retrySources}>Retry</button>
            </p>
          )}

          <QuickActions />
        </div>

        <aside className="hm-aside">
          {/* Renders nothing at all if the task dashboard will not load — one
              failing source must not cost the rest of the page. */}
          <TaskWidgets />

          <ActivityFeed limit={isMobile ? 3 : 5} />

          {/* Asked once a quarter, and only of people who have used the
              workspace for a week -- a rating on day one measures the
              sign-in page, not the product. */}
          {usedForAWeek && (
            <RateThis feature={`overall:${quarter}`} question="How is the workspace working for you?" />
          )}

          <section className="hm-sec" aria-labelledby="hm-glance-h">
            <div className="hm-sec-h">
              <h2 id="hm-glance-h">Today at a glance</h2>
            </div>
            <div className="hm-strips">
              <StatusStrip />
              <UnreadNotices />
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
};

export default Home;
