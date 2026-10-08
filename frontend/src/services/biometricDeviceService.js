import api from './api';

/**
 * Organization Settings -> Attendance -> Biometric Devices.
 *
 * NOTHING HERE TAKES AN ORGANIZATION. Every endpoint resolves the tenant from
 * the session, so a device belonging to somebody else cannot be addressed,
 * let alone synced. The connection test and "Sync now" run on the SERVER --
 * the browser never talks to the terminal -- which is why the address you
 * enter must be reachable from where this platform runs.
 */
export const biometricDeviceService = {
  dashboard: () => api.get('/biometric/devices/dashboard/'),
  create: (fields) => api.post('/biometric/devices/', fields),
  update: (id, fields) => api.patch(`/biometric/devices/${id}/`, fields),
  remove: (id) => api.delete(`/biometric/devices/${id}/`),

  /** Test the form's address before saving. Reads, never writes. */
  testUnsaved: (fields) => api.post('/biometric/devices/test-connection/', fields),
  /** Test a saved device; records the result and pins its serial. */
  test: (id) => api.post(`/biometric/devices/${id}/test-connection/`),
  /** Pull users and attendance now. Can take a while on a first sync. */
  sync: (id) => api.post(`/biometric/devices/${id}/sync/`, null, { timeout: 180000 }),
  resetIdentity: (id) => api.post(`/biometric/devices/${id}/reset-identity/`),

  syncLogs: (id, limit = 25) => api.get(`/biometric/devices/${id}/sync-logs/`, { params: { limit } }),
  users: (id, mapped) => api.get(`/biometric/devices/${id}/users/`,
    { params: mapped === undefined ? {} : { mapped } }),

  // Employee mapping (existing mapping API).
  map: (mappingId, user) => api.post(`/biometric/mappings/${mappingId}/map/`, { user }),
  unmap: (mappingId) => api.delete(`/biometric/mappings/${mappingId}/`),
  suggestions: (mappingId) => api.get(`/biometric/mappings/${mappingId}/suggestions/`),
  autoMatch: (device, apply) => api.post('/biometric/mappings/auto-match/', { device, apply }),

  // Attendance mode.
  mode: () => api.get('/attendance/settings/mode/'),
  setMode: (attendanceMode) => api.patch('/attendance/settings/mode/',
    { attendance_mode: attendanceMode }),
};

export default biometricDeviceService;
