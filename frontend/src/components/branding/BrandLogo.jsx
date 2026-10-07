import React from 'react';
import { useBranding } from '../../hooks/useBranding';
import { PLATFORM_LOGO, PLATFORM_NAME } from '../../config/platform';

/**
 * Phase S9 Part 2: the logo, wherever one appears inside the application.
 *
 * WHY A COMPONENT AND NOT EIGHT EDITS. `/NIF.png` was written literally into
 * the header and into seven print letterheads across the leave module. Each
 * was correct for a single-tenant deployment and each is a customer's leave
 * application printed under another organization's logo now. A component is
 * the only version of this fix that the ninth screen cannot miss.
 *
 * `variant` picks which uploaded image to prefer -- a letterhead logo is
 * often a wide monochrome lockup where the header's is a square mark -- and
 * falls back through the primary logo to the shipped one, so a customer who
 * uploaded only one image gets it everywhere.
 */
const BrandLogo = ({ variant = 'primary', alt, className, ...rest }) => {
  const { branding } = useBranding();
  const preferred = variant === 'letterhead' ? branding?.logo_letterhead : null;
  const uploaded = preferred || branding?.logo_primary;
  const label = alt !== undefined
    ? alt
    : `${branding?.display_name || PLATFORM_NAME} logo`;
  // A CUSTOMER WITHOUT A LOGO IS STILL THE CUSTOMER. This fell through to
  // the platform's logo, so a new workspace opened with "Buddhi Labs" in its
  // own header until somebody uploaded an image -- the first thing a new
  // customer saw after the welcome email told them the workspace was theirs.
  // Their initials, in their colour, until they upload one. The platform's
  // logo remains the answer only where there is no tenant at all.
  if (!uploaded && branding?.display_name) {
    const initials = branding.display_name.split(/\s+/).filter(Boolean)
      .slice(0, 2).map((word) => word[0].toUpperCase()).join('');
    return (
      <span className={`brand-monogram${className ? ` ${className}` : ''}`}
            role={label ? 'img' : undefined}
            aria-label={label || undefined}
            aria-hidden={label ? undefined : true}
            style={branding.color_primary ? { background: branding.color_primary } : undefined}>
        {initials}
      </span>
    );
  }
  return <img src={uploaded || PLATFORM_LOGO} alt={label} className={className} {...rest} />;
};

export default BrandLogo;
