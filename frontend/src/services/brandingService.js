import api from './api';

/**
 * Phase S9: a customer's own branding and custom domains.
 *
 * TWO AUDIENCES, ONE MODULE, DELIBERATELY. Branding and domains are the same
 * job from the customer's point of view -- "make this look and feel like
 * ours" -- and splitting them would mean two settings pages that both
 * half-answer it.
 *
 * NOTHING HERE TAKES AN ORGANIZATION. Every endpoint resolves the tenant
 * from the session, so a request about somebody else's brand or somebody
 * else's hostname cannot be written, let alone sent.
 */
export const brandingService = {
  /** The branding every member may read -- the whole application themes on it. */
  get: () => api.get('/tenant/branding/'),

  /** Set names, colours and copy. Administrator only, server-side. */
  update: (fields) => api.patch('/tenant/branding/', fields),

  /**
   * Replace one image. Multipart, because it carries a file.
   *
   * The field name travels in the body rather than the URL so one endpoint
   * covers all five images; the server checks it against an allow-list,
   * because a field name from a browser is a column name otherwise.
   */
  uploadAsset: (field, file) => {
    const body = new FormData();
    body.append('field', field);
    body.append('image', file);
    // WITHOUT THIS HEADER the body ships as application/json, Django's
    // parser finds no files, and the endpoint answers 400 in ten
    // milliseconds without reading the image. Phase S9 shipped this
    // line without it: the branding upload has never worked in a
    // browser. `test/uploads.guard.test.js` exists for exactly this
    // class of bug and missed it, because it only matched a variable
    // literally named `form`.
    return api.post('/tenant/branding/asset/', body,
      { headers: { 'Content-Type': 'multipart/form-data' } });
  },

  /** Their custom domains, each with the DNS records to publish. */
  domains: () => api.get('/tenant/domains/'),

  /** Claim a hostname. Resolves to nothing until DNS proves it is theirs. */
  claimDomain: (hostname, method) =>
    api.post('/tenant/domains/', { hostname, method }),

  /** Ask the platform to look the proof up in DNS now. */
  verifyDomain: (hostname) =>
    api.post(`/tenant/domains/${encodeURIComponent(hostname)}/`, {}),

  /** Withdraw a claim. */
  removeDomain: (hostname) =>
    api.delete(`/tenant/domains/${encodeURIComponent(hostname)}/`),
};

export default brandingService;
