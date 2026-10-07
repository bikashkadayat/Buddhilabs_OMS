import api from './api';

// DRF list endpoints are paginated ({count, results}); unwrap to the array.
const list = (res) => (Array.isArray(res.data) ? res.data : res.data?.results || []);

export const inventoryService = {
  // Categories
  categories: async () => list(await api.get('/inventory/categories/')),

  // Items
  items: async (params = {}) => list(await api.get('/inventory/items/', { params })),
  item: async (id) => (await api.get(`/inventory/items/${id}/`)).data,
  createItem: async (payload) => (await api.post('/inventory/items/', payload)).data,
  updateItem: async (id, payload) => (await api.patch(`/inventory/items/${id}/`, payload)).data,
  // deleteItem removed (Phase ASSET-LIFECYCLE-DISPOSAL): assets are never deleted,
  // and the server now refuses. An asset leaves the books through a disposal request.
  assignItem: async (id, payload) => (await api.post(`/inventory/items/${id}/assign/`, payload)).data,
  handoverItem: async (id, payload) => (await api.post(`/inventory/items/${id}/handover/`, payload)).data,
  returnItem: async (id, payload = {}) => (await api.post(`/inventory/items/${id}/return/`, payload)).data,
  assignments: async (id) => list(await api.get(`/inventory/items/${id}/assignments/`)),
  // Cross-item "who has what" board (managers) + employee "My Assigned Assets".
  board: async (params = {}) => list(await api.get('/inventory/assignments/', { params })),
  myAssets: async () => list(await api.get('/inventory/assignments/mine/')),
  assignmentReceipt: async (id) => {
    const res = await api.get(`/inventory/items/${id}/assignment-receipt/`, { responseType: 'blob' });
    const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
    window.open(url, '_blank', 'noopener');
    setTimeout(() => window.URL.revokeObjectURL(url), 60000);
  },

  // Take-out requests
  takeouts: async (params = {}) => list(await api.get('/inventory/takeouts/', { params })),
  // Phase 70.19-A. The take-out selector's own endpoint — NOT `items()`, which is
  // the asset register and is denied (403) to everyone who does not run the store.
  // Returns the envelope whole ({count, scope, empty_reason, results}) rather than
  // unwrapping to an array: `empty_reason` is how the server explains an empty
  // dropdown, and `list()` would throw it away and leave the UI guessing again.
  takeoutEligibleAssets: async (params = {}) =>
    (await api.get('/inventory/takeout/eligible-assets/', { params })).data,
  createTakeout: async (payload) => (await api.post('/inventory/takeouts/', payload)).data,
  approveTakeout: async (id, remarks = '') =>
    (await api.post(`/inventory/takeouts/${id}/approve/`, { remarks })).data,
  rejectTakeout: async (id, remarks) =>
    (await api.post(`/inventory/takeouts/${id}/reject/`, { remarks })).data,
  markReturned: async (id) => (await api.post(`/inventory/takeouts/${id}/mark_returned/`, {})).data,

  // Gate pass PDF (blob) — open in a new tab.
  gatePass: async (id) => {
    const res = await api.get(`/inventory/takeouts/${id}/gate-pass/`, { responseType: 'blob' });
    const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
    window.open(url, '_blank', 'noopener');
    setTimeout(() => window.URL.revokeObjectURL(url), 60000);
  },

  // Active employees (managers only) with name + department for the assign UI.
  employees: async () => list(await api.get('/inventory/employees/')),
};

/**
 * Phase 70 - the asset lifecycle, the two approval workflows, maintenance,
 * reports, QR and the employee profile.
 *
 * Appended rather than folded into the object above, so the pre-existing surface
 * stays diffable - the same reason the backend kept `lifecycle.py` beside
 * `services.py`.
 */
export const assetLifecycle = {
  // --- dashboard, reports, profile ---
  dashboard: async () => (await api.get('/inventory/dashboard/')).data,
  report: async (name) => (await api.get(`/inventory/reports/${name}/`)).data,
  // `download`, not `format`: DRF reserves `format` for content negotiation, so
  // `?format=pdf` 404s in the router before the view is ever reached.
  reportPdf: async (name) =>
    (await api.get(`/inventory/reports/${name}/`,
      { params: { download: 'pdf' }, responseType: 'blob' })).data,
  profile: async (userId) =>
    (await api.get(userId ? `/inventory/profile/${userId}/` : '/inventory/profile/')).data,

  // --- one asset ---
  history: async (id) => (await api.get(`/inventory/items/${id}/history/`)).data,
  qr: async (id) => (await api.get(`/inventory/items/${id}/qr/`)).data,
  scan: async (code) =>
    (await api.get('/inventory/scan/', { params: { code } })).data,
  lifecycleAction: async (id, verb, payload = {}) =>
    (await api.post(`/inventory/items/${id}/lifecycle/${verb}/`, payload)).data,

  // --- asset requests (70.5) ---
  requests: async (params = {}) => list(await api.get('/inventory/requests/', { params })),
  request: async (id) => (await api.get(`/inventory/requests/${id}/`)).data,
  createRequest: async (payload) => (await api.post('/inventory/requests/', payload)).data,
  supervisorDecision: async (id, payload) =>
    (await api.post(`/inventory/requests/${id}/supervisor/`, payload)).data,
  inventoryDecision: async (id, payload) =>
    (await api.post(`/inventory/requests/${id}/inventory/`, payload)).data,
  handOver: async (id, payload) =>
    (await api.post(`/inventory/requests/${id}/handover/`, payload)).data,
  acceptAsset: async (id, payload) =>
    (await api.post(`/inventory/requests/${id}/accept/`, payload)).data,
  cancelRequest: async (id, payload) =>
    (await api.post(`/inventory/requests/${id}/cancel/`, payload)).data,

  // --- returns (70.6) ---
  returns: async (params = {}) => list(await api.get('/inventory/returns/', { params })),
  createReturn: async (payload) => (await api.post('/inventory/returns/', payload)).data,
  verifyReturn: async (id, payload) =>
    (await api.post(`/inventory/returns/${id}/verify/`, payload)).data,
  inspectReturn: async (id, payload) =>
    (await api.post(`/inventory/returns/${id}/inspect/`, payload)).data,
  acceptReturn: async (id, payload) =>
    (await api.post(`/inventory/returns/${id}/accept/`, payload)).data,
  rejectReturn: async (id, payload) =>
    (await api.post(`/inventory/returns/${id}/reject/`, payload)).data,
  returnForm: async (id) =>
    (await api.get(`/inventory/returns/${id}/form/`, { responseType: 'blob' })).data,

  // --- maintenance (70.8) ---
  tickets: async (params = {}) => list(await api.get('/inventory/maintenance/', { params })),
  reportFault: async (payload) => (await api.post('/inventory/maintenance/', payload)).data,
  assignTicket: async (id, payload) =>
    (await api.post(`/inventory/maintenance/${id}/assign/`, payload)).data,
  startTicket: async (id, payload) =>
    (await api.post(`/inventory/maintenance/${id}/start/`, payload)).data,
  completeTicket: async (id, payload) =>
    (await api.post(`/inventory/maintenance/${id}/complete/`, payload)).data,
  returnToService: async (id, payload) =>
    (await api.post(`/inventory/maintenance/${id}/return-to-service/`, payload)).data,
  cancelTicket: async (id, payload) =>
    (await api.post(`/inventory/maintenance/${id}/cancel/`, payload)).data,

  // --- custody transfers (Phase ASSET-CUSTODY-TRANSFER) ---
  transfers: async (params = {}) => list(await api.get('/inventory/transfers/', { params })),
  transfer: async (id) => (await api.get(`/inventory/transfers/${id}/`)).data,
  transferOptions: async () => (await api.get('/inventory/transfer-options/')).data,
  createTransfer: async (payload) => (await api.post('/inventory/transfers/', payload)).data,
  updateTransfer: async (id, payload) =>
    (await api.patch(`/inventory/transfers/${id}/`, payload)).data,
  submitTransfer: async (id) =>
    (await api.post(`/inventory/transfers/${id}/submit/`)).data,
  approveTransfer: async (id, payload) =>
    (await api.post(`/inventory/transfers/${id}/approve/`, payload)).data,
  rejectTransfer: async (id, payload) =>
    (await api.post(`/inventory/transfers/${id}/reject/`, payload)).data,
  cancelTransfer: async (id, payload) =>
    (await api.post(`/inventory/transfers/${id}/cancel/`, payload)).data,
  attachToTransfer: async (id, files) => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    // multipart explicitly: the shared client defaults to JSON, and a FormData
    // body sent under that header reaches the server with no files in it.
    return (await api.post(`/inventory/transfers/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data;
  },

  // --- disposal and the asset's whole life (Phase ASSET-LIFECYCLE-DISPOSAL) ---
  disposals: async (params = {}) => list(await api.get('/inventory/disposals/', { params })),
  disposal: async (id) => (await api.get(`/inventory/disposals/${id}/`)).data,
  disposalOptions: async () => (await api.get('/inventory/disposal-options/')).data,
  createDisposal: async (payload) => (await api.post('/inventory/disposals/', payload)).data,
  submitDisposal: async (id) =>
    (await api.post(`/inventory/disposals/${id}/submit/`)).data,
  approveDisposal: async (id, payload) =>
    (await api.post(`/inventory/disposals/${id}/approve/`, payload)).data,
  rejectDisposal: async (id, payload) =>
    (await api.post(`/inventory/disposals/${id}/reject/`, payload)).data,
  cancelDisposal: async (id, payload) =>
    (await api.post(`/inventory/disposals/${id}/cancel/`, payload)).data,
  attachToDisposal: async (id, files) => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    // multipart explicitly, for the same reason as attachToTransfer above.
    return (await api.post(`/inventory/disposals/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data;
  },
  // --- Asset Visibility (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD) ---
  visibilityOptions: async () => (await api.get('/inventory/visibility-options/')).data,
  lifecycleSummary: async (itemId) =>
    (await api.get(`/inventory/items/${itemId}/lifecycle-summary/`)).data,

  // --- exit clearance and the custody record ---
  exitClearance: async (employeeId) => (await api.get(
    employeeId ? `/inventory/exit-clearance/${employeeId}/` : '/inventory/exit-clearance/',
  )).data,
  custody: async (itemId) => (await api.get(`/inventory/items/${itemId}/custody/`)).data,
};
