import api from './api';

const unwrap = (res) => (Array.isArray(res.data) ? res.data : (res.data?.results ?? []));

export const notificationService = {
  list: async (params = {}) => unwrap(await api.get('/notifications/', { params })),
  unreadCount: async () => (await api.get('/notifications/unread-count/')).data.unread,
  markRead: async (id) => (await api.post(`/notifications/${id}/read/`)).data,
  markAllRead: async () => (await api.post('/notifications/mark-all-read/')).data,
  remove: async (id) => { await api.delete(`/notifications/${id}/`); },
  getPreferences: async () => (await api.get('/notifications/preferences/')).data,
  setPreference: async (payload) => (await api.post('/notifications/preferences/', payload)).data,
};

export default notificationService;
