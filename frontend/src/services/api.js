import axios from 'axios';

// NO GLOBAL Content-Type (Phase MEMO-V1.0-PRODUCTION-HARDENING).
//
// This instance used to declare `Content-Type: application/json` for every
// request. Axios already sends that for a plain-object body, so the default
// bought nothing — but it was silently applied to FormData too, which shipped
// multipart bodies labelled as JSON. Django's parser found no files and
// answered 400 in about ten milliseconds without reading one. Memo and minute
// uploads were broken that way for the life of the feature; circular and task
// only worked because they happened to override the header.
//
// Letting axios infer per request removes the trap rather than guarding it:
// object bodies still go as JSON, FormData goes as multipart with the correct
// boundary, and a bodiless GET sends no content type at all.
const api = axios.create({
  baseURL: '/api/v1',
});

let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach(prom => {
    if (error) prom.reject(error);
    else prom.resolve(token);
  });
  failedQueue = [];
};

api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('accessToken');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  },
  (error) => Promise.reject(error)
);

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then(token => {
          // Mark queued requests as already-retried so a second 401 on the
          // replay does not re-enter the refresh branch (L2).
          originalRequest._retry = true;
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return api(originalRequest);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const refreshToken = localStorage.getItem('refreshToken');
      if (!refreshToken) {
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
        window.location.href = '/login';
        return Promise.reject(error);
      }

      try {
        const response = await axios.post('/api/v1/auth/refresh/', { refresh: refreshToken });
        const { access, refresh } = response.data;
        localStorage.setItem('accessToken', access);
        // Refresh rotation is ON server-side (ROTATE_REFRESH_TOKENS +
        // BLACKLIST_AFTER_ROTATION): the old refresh token is blacklisted the
        // moment it is used, so we MUST persist the new one it returns. Dropping
        // it left the next cycle presenting a dead token -> forced logout (C1).
        if (refresh) localStorage.setItem('refreshToken', refresh);
        api.defaults.headers.common.Authorization = `Bearer ${access}`;
        processQueue(null, access);
        originalRequest.headers.Authorization = `Bearer ${access}`;
        return api(originalRequest);
      } catch (refreshError) {
        processQueue(refreshError, null);
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
        window.location.href = '/login';
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default api;
