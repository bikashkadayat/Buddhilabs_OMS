import api from './api';

// Public self-registration removed in Phase 2.5. Accounts are created by an
// administrator via User Management.
export const authService = {
  login: async (email, password) => {
    return await api.post('/auth/login/', { email, password });
  },

  me: async () => {
    return await api.get('/auth/user/');
  },

  // Blacklist the refresh token server-side so it can't mint new access tokens
  // after logout (M5/M3). Needs a valid access token (still in storage) + refresh.
  logout: async (refresh) => {
    return await api.post('/auth/logout/', { refresh });
  },

  changePassword: async (currentPassword, newPassword) => {
    return await api.post('/auth/change-password/', {
      current_password: currentPassword,
      new_password: newPassword,
    });
  },
};
