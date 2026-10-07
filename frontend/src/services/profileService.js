import api from './api';

/** Self-service profile: read, edit own editable fields, and manage the photo. */
export const profileService = {
  me: async () => (await api.get('/profile/me/')).data,
  update: async (payload) => (await api.patch('/profile/me/', payload)).data,
  uploadPhoto: async (file) => {
    const fd = new FormData();
    fd.append('photo', file);
    // Content-Type undefined -> axios/browser set multipart with the boundary.
    return (await api.post('/profile/me/photo/', fd, { headers: { 'Content-Type': undefined } })).data;
  },
  removePhoto: async () => (await api.delete('/profile/me/photo/')).data,
};

export default profileService;
