import api from './api';

const unwrap = (res) => (Array.isArray(res.data) ? res.data : (res.data?.results ?? []));

/** Admin User Management (Phase 2.5) against /api/v1/users/admin/users/. */
export const userMgmtService = {
  list: async (params = {}) => unwrap(await api.get('/users/admin/users/', { params })),
  get: async (id) => (await api.get(`/users/admin/users/${id}/`)).data,
  create: async (payload) => (await api.post('/users/admin/users/', payload)).data,
  update: async (id, payload) => (await api.patch(`/users/admin/users/${id}/`, payload)).data,
  resetPassword: async (id) => (await api.post(`/users/admin/users/${id}/reset-password/`, {})).data,
  deactivate: async (id) => (await api.post(`/users/admin/users/${id}/deactivate/`, {})).data,
  activate: async (id) => (await api.post(`/users/admin/users/${id}/activate/`, {})).data,
  changeRole: async (id, role) => (await api.post(`/users/admin/users/${id}/change-role/`, { role })).data,
  // Permanent delete — backend allows it only for INACTIVE users (409 otherwise).
  remove: async (id) => (await api.delete(`/users/admin/users/${id}/`)).data,
  // Active departments for the employee-creation dropdown.
  departments: async () => unwrap(await api.get('/leaves/admin/departments/')),
};

export default userMgmtService;
