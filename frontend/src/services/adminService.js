import api from './api';
import { unwrapPaginated } from './utils';

export const adminService = {
  getUsers: async () => {
    const response = await api.get('/users/');
    return unwrapPaginated(response);
  },

  createUser: async (data) => {
    const response = await api.post('/admin/users/', data);
    return response.data;
  },

  updateUser: async (id, data) => {
    const response = await api.patch(`/admin/users/${id}/`, data);
    return response.data;
  },

  deleteUser: async (id) => {
    await api.delete(`/admin/users/${id}/`);
  },

  getLeaves: async () => {
    const response = await api.get('/admin/leaves/');
    return unwrapPaginated(response);
  },

  createLeave: async (data) => {
    const response = await api.post('/admin/leaves/', data);
    return response.data;
  },

  updateLeave: async (id, data) => {
    const response = await api.patch(`/admin/leaves/${id}/`, data);
    return response.data;
  },

  deleteLeave: async (id) => {
    await api.delete(`/admin/leaves/${id}/`);
  },

  getBalances: async () => {
    const response = await api.get('/admin/balances/');
    return unwrapPaginated(response);
  },

  createBalance: async (data) => {
    const response = await api.post('/admin/balances/', data);
    return response.data;
  },

  updateBalance: async (id, data) => {
    const response = await api.patch(`/admin/balances/${id}/`, data);
    return response.data;
  },

  deleteBalance: async (id) => {
    await api.delete(`/admin/balances/${id}/`);
  },

  getStats: async () => {
    const response = await api.get('/admin/stats/');
    return response.data;
  }
};