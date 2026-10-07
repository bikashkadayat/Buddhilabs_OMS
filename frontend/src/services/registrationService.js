import api from './api';

/**
 * Phase S7: the public self-service endpoints.
 *
 * SEPARATE FROM EVERY OTHER SERVICE, like platformService is, and for the
 * mirror-image reason: nothing here is authenticated. These three calls are
 * the only ones in this application that a stranger makes, so keeping them in
 * one file means "what can the public reach?" has one file as its answer.
 *
 * NOTE WHAT IS NOT HERE. No payment, no plan selection, no card. A
 * self-registered workspace lands on a trial and an operator decides what
 * happens at the end of it.
 */
export const registrationService = {
  /**
   * What the public offer is. Phase S11 Part 7.
   *
   * The same unauthenticated endpoint the login page reads for branding; it
   * also carries `registration_open` and `trial_days`. The signup page used
   * to promise nothing and disclaim a card, so the trial — the actual offer —
   * appeared in the verification email and on the first-login page but not on
   * the screen where somebody decides whether to sign up.
   *
   * Read rather than written into the page, so changing
   * TENANCY_SELF_SERVICE_TRIAL_DAYS cannot leave the copy advertising a
   * trial length the product no longer gives.
   */
  availability: () => api.get('/tenant/public/branding/'),

  /** Live subdomain check. Debounce the caller — this is rate limited. */
  checkSlug: (slug) =>
    api.get('/register/slug/', { params: { slug } }),

  /**
   * Submit a registration. Answers 202: NOTHING has been created yet.
   *
   * The response is deliberately the same whether the address was new or
   * already had a registration pending — a signup form that says "this email
   * is already registered" tells a stranger which addresses have workspaces
   * here, one request at a time.
   */
  register: (body) => api.post('/register/', body),

  /** Verify the emailed token. On success the workspace now exists. */
  verify: (token) => api.post('/register/verify/', { token }),
};

/**
 * Whether this deployment offers self-service signup at all.
 *
 * Read from the unauthenticated branding endpoint the sign-in page already
 * calls, so the link to /register is only shown where it works. The backend
 * answers 404 on the registration routes when the capability is off, and a
 * link to a 404 is worse than no link.
 */
export const registrationOpen = async () => {
  try {
    const { data } = await api.get('/tenant/public/branding/');
    return Boolean(data?.registration_open);
  } catch {
    return false;
  }
};

export default registrationService;
