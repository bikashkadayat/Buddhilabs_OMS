import { useEffect, useState } from 'react';
import api from '../services/api';
import { applyTheme, paletteFor } from '../utils/brandTheme';

/**
 * The tenant's own branding, BEFORE anybody has logged in (Phase S6 Part 7).
 *
 * A login page has to know whose login page it is, and at that moment there is
 * no session to ask. So this reads the one unauthenticated tenancy endpoint,
 * which answers for the host it was asked on and returns exactly five fields:
 * a name, a login logo, two colours and a tagline.
 *
 * WHY IT RETURNS NULL RATHER THAN A DEFAULT OBJECT. The caller must be able to
 * tell "no answer yet / unknown host" from "this tenant has no custom
 * branding", because those render differently: the first keeps the shipped
 * NIF branding, the second also keeps it, but a half-applied colour with no
 * logo looks like a broken page. One null covers both.
 *
 * NEVER REJECTS. A failed branding lookup must not stop anybody signing in --
 * the page falls back to the platform's own brand.
 *
 * IT ALSO SETS THE TAB ICON, which is not cosmetic for a customer who has
 * uploaded one: the favicon could be uploaded through the console and stored
 * against the tenant, and then nothing ever served it, so every customer's
 * browser tab showed the platform's own icon. There is no React-rendered
 * element for a favicon -- it is a <link> in the document head -- so it is
 * applied here, where the answer arrives, and reverted on unmount so a sign
 * out does not leave one customer's icon on the next page.
 */
export const useTenantBranding = ({ withMeta = false } = {}) => {
  const [branding, setBranding] = useState(null);
  // Whether public signup is open, answered for EVERY host -- including the
  // platform's own, which is the only place the link belongs. Kept apart
  // from `branding` so that `branding` still means "a known tenant".
  const [registrationOpen, setRegistrationOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api.get('/tenant/public/branding/')
      .then((response) => {
        if (!alive) return;
        // `known: false` is an unresolved host, which the endpoint answers
        // deliberately rather than 404ing -- a 404 would turn the login page
        // into a customer-list oracle.
        setRegistrationOpen(Boolean(response.data?.registration_open));
        if (response.data?.known) setBranding(response.data);
      })
      .catch(() => { /* fall back to the shipped branding */ });
    return () => { alive = false; };
  }, []);

  // Phase S9 Part 2. The pre-login payload has carried `color_primary` and
  // `color_secondary` since S6 and NOTHING EVER READ THEM: the login page
  // showed the tenant's logo on the platform's blue. Applying them here
  // themes the one screen that exists before there is a session, using the
  // same derivation the authenticated provider uses, so a customer's login
  // page and their dashboard are the same colour.
  const primary = branding?.color_primary;
  const secondary = branding?.color_secondary;
  useEffect(() => {
    if (!primary && !secondary) return undefined;
    return applyTheme(paletteFor({ primary, secondary }));
  }, [primary, secondary]);

  useEffect(() => {
    const href = branding?.favicon;
    if (!href) return undefined;
    const link = document.querySelector("link[rel~='icon']");
    if (!link) return undefined;
    const shipped = link.getAttribute('href');
    link.setAttribute('href', href);
    return () => { link.setAttribute('href', shipped); };
  }, [branding?.favicon]);

  return withMeta ? { branding, registrationOpen } : branding;
};

export default useTenantBranding;
