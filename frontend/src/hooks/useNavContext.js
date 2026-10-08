/**
 * Which navigation context the current route belongs to (Phase E).
 *
 * Derived from the URL, never stored. That is the whole point: today the
 * sidebar's shape depends on which sections a user last expanded, persisted in
 * localStorage, so the same URL can look different to two people or to the same
 * person tomorrow. Deriving it means a link always lands on the same rail.
 */
import { useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from './useAuth';
import { memoService } from '../services/memoService';
import { minuteService } from '../services/minuteService';
import { circularService } from '../services/circularService';
import { taskService } from '../services/taskService';
import { appraisalService } from '../services/appraisalService';
import { draftService } from '../services/draftService';
import { useCorrectionCounts } from './useWorkforce';
import { supportService } from '../services/supportService';
import { useWorkQueue } from './useWorkQueue';
import { CONTEXTS, contextForPath, visibleItems } from '../components/layout/navConfig';

/**
 * Badge counts, gathered once for whichever rail is showing.
 *
 * ONE CACHE KEY PER ENDPOINT: ['<module>', 'dashboard'], the same key the
 * module's own dashboard page uses. The rail used to call it 'sidebar-counts',
 * which React Query treated as a different query from the page's - so every
 * module dashboard fetched its counts TWICE, once for the page and once for the
 * badge (found in the production build, Phase OMS-FINAL-FREEZE-HARDENING).
 *
 * Every one fails quietly - a badge is never worth breaking navigation over,
 * which is the posture the previous sidebar took and the one thing about it
 * that should survive unchanged.
 */
const useNavCounts = () => {
  const { data: memo } = useQuery({
    queryKey: ['memos', 'dashboard'],
    queryFn: memoService.getDashboard,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  const { data: minute } = useQuery({
    queryKey: ['minutes', 'dashboard'],
    queryFn: minuteService.getDashboard,
    staleTime: 60_000,
    retry: false,
  });
  const { data: circular } = useQuery({
    queryKey: ['circulars', 'dashboard'],
    queryFn: circularService.getDashboard,
    staleTime: 60_000,
    retry: false,
  });
  const { data: task } = useQuery({
    queryKey: ['tasks', 'dashboard'],
    queryFn: taskService.getDashboard,
    staleTime: 60_000,
    retry: false,
  });
  // Phase APM-03b. Shares the cache key the appraisal pages and the Home card
  // already use, so the rail badge costs no extra request on any screen that
  // has already asked.
  const { data: appraisalDash } = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
    staleTime: 60_000,
    retry: false,
  });
  const { data: drafts } = useQuery({
    queryKey: ['drafts', 'list'],
    queryFn: draftService.listDrafts,
    staleTime: 60_000,
    retry: false,
  });
  // Help & Support: unread ticket replies and unseen What's New posts.
  const { data: support } = useQuery({
    queryKey: ['support', 'badges'],
    queryFn: supportService.badges,
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });
  const { data: corrections } = useCorrectionCounts();
  // The queue is already fetched by Home and /queue; this only reads the cache.
  const { counts } = useWorkQueue({ enabled: false });

  return useMemo(() => ({
    memo,
    minute,
    circular,
    task,
    // Flattened to the two figures the badges read: whether the person's own
    // appraisal is waiting on them, and how many of their team's are with them.
    appraisal: appraisalDash && {
      awaiting_me: appraisalDash.employee?.awaiting_me || false,
      pending_reviews: appraisalDash.manager?.pending_reviews || 0,
    },
    corrections,
    support,
    queue: counts?.total || null,
    drafts: Array.isArray(drafts) ? drafts.length || null : (drafts?.results?.length || null),
  }), [memo, minute, circular, task, appraisalDash, corrections, support, counts, drafts]);
};

export const useNavContext = () => {
  const { pathname } = useLocation();
  const { role } = useAuth();
  const counts = useNavCounts();

  const key = useMemo(() => contextForPath(pathname), [pathname]);
  const context = CONTEXTS[key] || CONTEXTS.workspace;
  const items = useMemo(() => visibleItems(key, role), [key, role]);

  return {
    key,
    context,
    items,
    counts,
    isWorkspace: key === 'workspace',
  };
};

export default useNavContext;
