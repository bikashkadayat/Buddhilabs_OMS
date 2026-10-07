import api from './api';

/** Support Center and feedback: the customer talking to the platform team. */
export const supportService = {
  mine: () => api.get('/support/requests/').then((r) => r.data),
  send: (body) => api.post('/support/requests/', {
    page: typeof window !== 'undefined' ? window.location.pathname : '', ...body,
  }).then((r) => r.data),
};

export default supportService;
