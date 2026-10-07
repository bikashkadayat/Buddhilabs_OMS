import { useQuery } from '@tanstack/react-query';
import { monitoringService } from '../services/monitoringService';

/**
 * Monitoring hooks.
 *
 * Polled rather than streamed: the WebSocket carries attendance events, and a
 * page that exists to tell you whether the system is healthy must not depend on
 * the part of the system most likely to be unhealthy.
 *
 * `retry: false` matters here too. React Query's default backoff would keep a
 * failed health check spinning for half a minute before showing anything — on
 * this page in particular, a failure IS the answer and should be shown at once.
 */
const REFRESH_MS = 30_000;

export const KEYS = {
  health: ['monitoring', 'health'],
  cron: ['monitoring', 'cron'],
  alerts: ['monitoring', 'alerts'],
};

export const useSystemHealth = () =>
  useQuery({
    queryKey: KEYS.health,
    queryFn: monitoringService.health,
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
    retry: false,
    staleTime: 0,
  });

export const useCronStatus = () =>
  useQuery({
    queryKey: KEYS.cron,
    queryFn: monitoringService.cron,
    refetchInterval: REFRESH_MS,
    retry: false,
  });

export const useAlertStatus = () =>
  useQuery({
    queryKey: KEYS.alerts,
    queryFn: monitoringService.alerts,
    refetchInterval: REFRESH_MS,
    retry: false,
  });
