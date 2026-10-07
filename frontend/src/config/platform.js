/**
 * Who the PLATFORM is — as opposed to who a tenant is.
 *
 * Phase BRAND-MIGRATION. These values were literals in six components, so
 * "Nepal Internet Foundation" appeared in the login page, the header, the
 * branding fallback and the console independently, and a brand change meant
 * finding all six. One of them had already drifted.
 *
 * NOTHING HERE IS TENANT BRANDING. Every value is the fallback used when a
 * tenant has supplied none of its own, plus the "Powered by" line that
 * appears under a tenant's brand rather than instead of it. A tenant with a
 * logo never shows this logo; a tenant with colours never uses this palette.
 * `hooks/useBranding.jsx` is where the two are combined, and it prefers the
 * tenant in every case.
 */
export const PLATFORM_NAME = 'Buddhi Labs';

/** The full lockup: symbol plus wordmark. Headers, login, the console. */
export const PLATFORM_LOGO = '/buddhi-labs.png';

/** The symbol alone, square. For anywhere a wordmark would be a smear. */
export const PLATFORM_MARK = '/buddhi-labs-mark.png';

/**
 * The attribution line carried on every tenant surface.
 *
 * Deliberately NOT a copyright notice. A tenant's login page belongs to the
 * tenant; this says who runs the software underneath it, which is a
 * different claim and the one the platform is entitled to make.
 */
export const POWERED_BY = `Powered by ${PLATFORM_NAME}`;
export const PLATFORM_THEME_KEY = 'platform:theme';
