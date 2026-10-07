import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { workforceService } from '../services/workforceService';

/**
 * React Query hooks for the workforce layer.
 *
 * Two conventions the pages rely on:
 *
 * 1. Every hook returns the standard { data, isLoading, isError, error, refetch }
 *    shape, so each page can render Skeleton / ErrorState / EmptyState exactly
 *    as the leave-records pages already do — and so tests can mock this module
 *    rather than the network.
 * 2. Mutations invalidate only what they can actually change. A blanket
 *    invalidateQueries() would refetch every dashboard on every click.
 */

export const KEYS = {
  me: ['workforce', 'me'],
  team: (params) => ['workforce', 'team', params ?? {}],
  hr: (params) => ['workforce', 'hr', params ?? {}],
  corrections: (params) => ['workforce', 'corrections', params ?? {}],
  correctionCounts: ['workforce', 'corrections', 'counts'],
  conflicts: (params) => ['workforce', 'conflicts', params ?? {}],
  wfh: (params) => ['workforce', 'wfh', params ?? {}],
  wfhRequests: (params) => ['workforce', 'wfh-requests', params ?? {}],
  compoff: ['workforce', 'compoff'],
  leavePreview: (params) => ['workforce', 'leave-preview', params ?? {}],
};

// ---------------------------------------------------------------------------
// dashboards
// ---------------------------------------------------------------------------
export const useMyWorkforce = () =>
  useQuery({ queryKey: KEYS.me, queryFn: workforceService.me });

export const useTeamDashboard = (params) =>
  useQuery({ queryKey: KEYS.team(params), queryFn: () => workforceService.team(params) });

export const useHRDashboard = (params) =>
  useQuery({ queryKey: KEYS.hr(params), queryFn: () => workforceService.hr(params) });

// ---------------------------------------------------------------------------
// corrections
// ---------------------------------------------------------------------------
export const useCorrections = (params) =>
  useQuery({
    queryKey: KEYS.corrections(params),
    queryFn: () => workforceService.corrections(params),
    placeholderData: (previous) => previous,   // no flash of empty on page change
  });

export const useCorrectionCounts = () =>
  useQuery({
    queryKey: KEYS.correctionCounts,
    queryFn: workforceService.correctionCounts,
  });

/**
 * Every correction mutation, sharing one invalidation policy.
 *
 * `touchesAttendance` matters: an HR approval rewrites the employee's
 * attendance row, so the personal and org dashboards must be refetched too. A
 * manager approval only moves the request between queues.
 */
const useCorrectionMutation = (mutationFn, { touchesAttendance = false } = {}) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['workforce', 'corrections'] });
      if (touchesAttendance) {
        client.invalidateQueries({ queryKey: KEYS.me });
        client.invalidateQueries({ queryKey: ['workforce', 'team'] });
        client.invalidateQueries({ queryKey: ['workforce', 'hr'] });
        client.invalidateQueries({ queryKey: ['attendance'] });
      }
    },
  });
};

export const useSubmitCorrection = () =>
  useCorrectionMutation((payload) => workforceService.submitCorrection(payload));

export const useApproveCorrection = () =>
  useCorrectionMutation(
    ({ id, remarks }) => workforceService.approveCorrection(id, remarks),
    // Stage-aware on the server; the HR stage is the one that writes
    // attendance, and we cannot tell which stage ran from here.
    { touchesAttendance: true },
  );

export const useRejectCorrection = () =>
  useCorrectionMutation(({ id, reason }) => workforceService.rejectCorrection(id, reason));

export const useCancelCorrection = () =>
  useCorrectionMutation(({ id }) => workforceService.cancelCorrection(id));

export const useRevertCorrection = () =>
  useCorrectionMutation(({ id, reason }) => workforceService.revertCorrection(id, reason),
    { touchesAttendance: true });

// ---------------------------------------------------------------------------
// conflicts
// ---------------------------------------------------------------------------
export const useConflicts = (params) =>
  useQuery({ queryKey: KEYS.conflicts(params), queryFn: () => workforceService.conflicts(params) });

export const useLeavePreview = (params, enabled = true) =>
  useQuery({
    queryKey: KEYS.leavePreview(params),
    queryFn: () => workforceService.leavePreview(params),
    enabled: Boolean(enabled && params?.start),
  });

// ---------------------------------------------------------------------------
// WFH
// ---------------------------------------------------------------------------
export const useWfhSummary = (params) =>
  useQuery({ queryKey: KEYS.wfh(params), queryFn: () => workforceService.wfhSummary(params) });

export const useWfhRequests = (params) =>
  useQuery({
    queryKey: KEYS.wfhRequests(params),
    queryFn: () => workforceService.wfhRequests(params),
    placeholderData: (previous) => previous,
  });

const useWfhMutation = (mutationFn) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['workforce', 'wfh'] });
      client.invalidateQueries({ queryKey: ['workforce', 'wfh-requests'] });
      // An approval can flip a day to WORK_FROM_HOME, so the dashboards move.
      client.invalidateQueries({ queryKey: KEYS.me });
      client.invalidateQueries({ queryKey: ['workforce', 'team'] });
      client.invalidateQueries({ queryKey: ['workforce', 'hr'] });
    },
  });
};

export const useSubmitWfh = () => useWfhMutation((payload) => workforceService.submitWfh(payload));
export const useApproveWfh = () => useWfhMutation(({ id, note }) => workforceService.approveWfh(id, note));
export const useRejectWfh = () => useWfhMutation(({ id, note }) => workforceService.rejectWfh(id, note));
export const useCancelWfh = () => useWfhMutation(({ id }) => workforceService.cancelWfh(id));

// ---------------------------------------------------------------------------
// comp-off
// ---------------------------------------------------------------------------
export const useCompOffSummary = () =>
  useQuery({ queryKey: KEYS.compoff, queryFn: workforceService.compOffSummary });

const useCompOffMutation = (mutationFn) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: KEYS.compoff });
      client.invalidateQueries({ queryKey: KEYS.me });
      client.invalidateQueries({ queryKey: ['workforce', 'hr'] });
    },
  });
};

export const useConfirmCompOff = () =>
  useCompOffMutation(({ id, note }) => workforceService.confirmCompOff(id, note));
export const useRejectCompOff = () =>
  useCompOffMutation(({ id, reason }) => workforceService.rejectCompOff(id, reason));
