import api from './api';

/**
 * Monitoring API (Phase 11). HR and Admin only — device health, backup state
 * and login-failure counts are infrastructure, and a department head has no
 * action to take on any of it.
 *
 * Deliberately separate from `/health/`, which is the container liveness probe
 * and must stay as dependency-free as possible.
 */
export const monitoringService = {
  health: async () => (await api.get('/monitoring/health/')).data,
  cron: async () => (await api.get('/monitoring/cron/')).data,
  alerts: async () => (await api.get('/monitoring/alerts/')).data,
};

export default monitoringService;
