import api from './api';
import { saveBlob } from './reportService';

/** PDF document endpoints (approved memos/leaves only). Responses are blobs. */
export const documentService = {
  leavePdf: (id) => api.get(`/leaves/${id}/pdf/`, { responseType: 'blob' }),
  leaveCertificate: (id) => api.get(`/leaves/${id}/certificate/`, { responseType: 'blob' }),
  memoPdf: (id) => api.get(`/memos/${id}/pdf/`, { responseType: 'blob' }),
  verifyUrl: (documentNumber) => `/api/v1/verify/${documentNumber}/`,
};

export { saveBlob };
export default documentService;
