import { useMutation, useQuery } from '@tanstack/react-query';
import { analyticsService } from '../services/analyticsService';

/**
 * React Query hooks for the analytics layer.
 *
 * Two conventions, both deliberate:
 *
 * 1. `staleTime` matches the server's cache TTL (5 minutes for a live window,
 *    1 minute for device health). Refetching more often than the server
 *    recomputes just repeats a cached response over the network.
 * 2. No WebSocket subscription anywhere in here. The live attendance stream
 *    exists for the operational dashboards; wiring it to analytics would
 *    invalidate on every punch and defeat the caching entirely. Phase 10 is
 *    explicitly not transaction processing.
 */

const FIVE_MINUTES = 5 * 60 * 1000;
const ONE_MINUTE = 60 * 1000;
const ONE_HOUR = 60 * 60 * 1000;

export const KEYS = {
  meta: ['analytics', 'meta'],
  executive: (params) => ['analytics', 'executive', params ?? {}],
  hr: (params) => ['analytics', 'hr', params ?? {}],
  management: (params) => ['analytics', 'management', params ?? {}],
  attendance: (params) => ['analytics', 'attendance', params ?? {}],
  departments: (params) => ['analytics', 'departments', params ?? {}],
  leave: (params) => ['analytics', 'leave', params ?? {}],
  wfh: (params) => ['analytics', 'wfh', params ?? {}],
  compOff: (params) => ['analytics', 'comp-off', params ?? {}],
  devices: (params) => ['analytics', 'devices', params ?? {}],
};

const useDashboardQuery = (key, queryFn, params, staleTime = FIVE_MINUTES) =>
  useQuery({
    queryKey: key,
    queryFn: () => queryFn(params),
    staleTime,
    // Changing the period must not blank the page: keeping the previous payload
    // on screen while the next one loads is the difference between a dashboard
    // and a flicker.
    placeholderData: (previous) => previous,
  });

export const useAnalyticsMeta = () =>
  useQuery({ queryKey: KEYS.meta, queryFn: analyticsService.meta, staleTime: ONE_HOUR });

export const useExecutiveAnalytics = (params) =>
  useDashboardQuery(KEYS.executive(params), analyticsService.executive, params);

export const useHrAnalytics = (params) =>
  useDashboardQuery(KEYS.hr(params), analyticsService.hr, params);

export const useManagementAnalytics = (params) =>
  useDashboardQuery(KEYS.management(params), analyticsService.management, params);

export const useAttendanceAnalytics = (params) =>
  useDashboardQuery(KEYS.attendance(params), analyticsService.attendance, params);

export const useDepartmentAnalytics = (params) =>
  useDashboardQuery(KEYS.departments(params), analyticsService.departments, params);

export const useLeaveAnalytics = (params) =>
  useDashboardQuery(KEYS.leave(params), analyticsService.leave, params);

export const useWfhAnalytics = (params) =>
  useDashboardQuery(KEYS.wfh(params), analyticsService.wfh, params);

export const useCompOffAnalytics = (params) =>
  useDashboardQuery(KEYS.compOff(params), analyticsService.compOff, params);

export const useDeviceAnalytics = (params) =>
  useDashboardQuery(KEYS.devices(params), analyticsService.devices, params, ONE_MINUTE);

/**
 * Queue an export and poll it to completion.
 *
 * Reuses the reports hub, so nothing here knows how a file is produced — only
 * that a run id becomes a signed download URL. Polling stops on ready, on
 * failure, or after the attempt cap, so a stuck run cannot poll forever.
 */
const POLL_INTERVAL_MS = 1200;
const MAX_POLLS = 50;

export const useAnalyticsExport = () =>
  useMutation({
    mutationFn: async ({ reportType, format, params }) => {
      const run = await analyticsService.requestExport(reportType, format, params);
      for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
         
        const status = await analyticsService.exportStatus(run.id);
        if (status.status === 'ready') return status;
        if (status.status === 'failed') {
          throw new Error(status.error || 'The export could not be generated.');
        }
         
        await new Promise((resolve) => { setTimeout(resolve, POLL_INTERVAL_MS); });
      }
      throw new Error('The export is taking longer than expected. '
        + 'It will appear in Reports → History when it finishes.');
    },
  });
