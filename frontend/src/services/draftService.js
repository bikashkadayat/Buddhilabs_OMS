import api from './api';

/**
 * Server-side draft snapshots (Phase 111.7).
 *
 * The server copy is what survives losing the device, logging out, or a session
 * expiring — the cases the local IndexedDB copy cannot help with. It stores an
 * opaque JSON blob and applies no field validation, which is what lets a
 * half-typed document be saved at all.
 */
export const draftService = {
  /** Everything unfinished, across all three modules. */
  listDrafts: async () => (await api.get('/drafts/')).data,

  /** The snapshot for one document, or null. Never throws on "nothing there". */
  getDraft: async (kind, documentKey) => (
    (await api.get(`/drafts/${kind}/${documentKey}/`)).data.draft
  ),

  /**
   * Upsert the snapshot. `milestone` opts the write into retained history;
   * an ordinary idle autosave omits it and simply overwrites.
   */
  saveDraft: async (kind, documentKey, payload, { milestone, deviceLabel } = {}) => (
    (await api.put(`/drafts/${kind}/${documentKey}/`, {
      payload,
      ...(milestone ? { milestone } : {}),
      ...(deviceLabel ? { device_label: deviceLabel } : {}),
    })).data
  ),

  /**
   * Remove the snapshot. `submitted` marks it as "became a real document"
   * rather than "the user threw it away", which the audit trail distinguishes.
   */
  discardDraft: async (kind, documentKey, { submitted = false } = {}) => (
    api.delete(`/drafts/${kind}/${documentKey}/${submitted ? '?submitted=1' : ''}`)
  ),

  getVersion: async (kind, documentKey, version) => (
    (await api.get(`/drafts/${kind}/${documentKey}/versions/${version}/`)).data
  ),

  restoreVersion: async (kind, documentKey, version) => (
    (await api.post(`/drafts/${kind}/${documentKey}/restore/`, { version })).data.draft
  ),

  /** Record that the user accepted a recovery offer (Phase 111.16). */
  markRecovered: async (kind, documentKey) => (
    api.post(`/drafts/${kind}/${documentKey}/recovered/`)
  ),
};

export default draftService;
