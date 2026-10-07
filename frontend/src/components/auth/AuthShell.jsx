import React from 'react';
import { PLATFORM_LOGO, PLATFORM_NAME, POWERED_BY } from '../../config/platform';

/**
 * Whose mark goes at the top of this page.
 *
 * A CUSTOMER WITHOUT A LOGO STILL GETS THEIR OWN PAGE. This fell back to the
 * platform's logo, so a newly created workspace opened on its first day with
 * "Buddhi Labs" in the largest type on the screen and the customer's own name
 * in small print under it -- on the page the welcome email had just told them
 * was theirs. Now an unbranded tenant gets a monogram of its own name, in its
 * own colour where it has one, until it uploads a logo. The platform's logo
 * is shown only where there is no tenant at all: the console host, or an
 * address that resolved to nobody.
 */
const monogram = (name) => (name || '')
  .split(/\s+/).filter(Boolean).slice(0, 2)
  .map((word) => word[0].toUpperCase()).join('');

export const BrandMark = ({ branding, className, decorative = false }) => {
  if (branding?.logo_login) {
    return (
      <img src={branding.logo_login} alt={decorative ? '' : branding.name}
           className={className} />
    );
  }
  if (branding?.name) {
    return (
      <span className={`${className} auth-monogram`}
            role={decorative ? undefined : 'img'}
            aria-label={decorative ? undefined : branding.name}
            aria-hidden={decorative || undefined}
            style={branding.color_primary
              ? { background: branding.color_primary } : undefined}>
        {monogram(branding.name)}
      </span>
    );
  }
  return (
    <img src={PLATFORM_LOGO} alt={decorative ? '' : PLATFORM_NAME}
         className={className} />
  );
};

// Single unified login for all roles (Phase 2.5). Public self-registration of
// EMPLOYEES stays removed: nobody signs themselves up as a member of an
// existing organization -- their administrator creates that account.
//
// Phase S7 added something different and easy to confuse with it: public
// registration of an ORGANIZATION. That creates a new workspace and its first
// administrator, never an account inside somebody else's. The link below is
// shown only where the deployment offers it.
/**
 * The sign-in family's frame: the customer's brand panel beside a quiet card.
 *
 * Shared by sign-in, forgot password and reset password, so all three look
 * like the same organization's door -- a reset page that suddenly wore the
 * platform's colours would be the moment a careful person stops trusting the
 * link in the email.
 */
const AuthShell = ({ branding, welcome, children }) => {
  const name = branding?.name || PLATFORM_NAME;
  const line = welcome || branding?.login_tagline
    || (branding
      ? `Attendance, leave, tasks and documents for everyone at ${branding.name}.`
      : 'Sign in to your organization’s workspace.');
  return (
    <div className="lg">
      <aside className="lg-brand" aria-hidden="true">
        <div className="lg-brand-glow" />
        <div className="lg-brand-body">
          <BrandMark branding={branding} className="lg-brand-mark" decorative />
          <p className="lg-brand-name">{name}</p>
          <p className="lg-brand-welcome">{line}</p>
        </div>
      </aside>
      <main className="lg-main">
        <div className="lg-card">
          <div className="lg-card-brand">
            <BrandMark branding={branding} className="lg-card-mark" decorative />
            <span>{name}</span>
          </div>
          {children}
        </div>
        <p className="lg-foot">{POWERED_BY}</p>
      </main>
    </div>
  );
};

export default AuthShell;
