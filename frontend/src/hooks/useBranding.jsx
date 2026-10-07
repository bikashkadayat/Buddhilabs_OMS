import React, { createContext, useContext, useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../services/api';
import { applyTheme, paletteFor } from '../utils/brandTheme';
import { PLATFORM_LOGO, PLATFORM_NAME } from '../config/platform';
import { useAuth } from './useAuth';

/**
 * Phase S9 Part 2: the tenant's branding, everywhere inside the application.
 *
 * THE SIBLING OF `useTenantBranding`, AND NOT A REPLACEMENT FOR IT. That one
 * reads the unauthenticated endpoint so a login page can tell whose login
 * page it is. This one reads the authenticated endpoint, which answers for
 * the organization the session belongs to rather than for a hostname -- and
 * that distinction is the whole reason there are two: a tenant reached on
 * the shared platform host has no identifying hostname, so pre-login
 * branding is unavailable there while post-login branding is not.
 *
 * IT THEMES THE DOCUMENT AS A SIDE EFFECT, which is unusual for a React
 * provider and is the only way to reach what React does not render: the
 * custom properties on :root that the entire stylesheet is built on. See
 * `utils/brandTheme.js`.
 *
 * NEVER THROWS, NEVER BLOCKS. A branding request that fails leaves the
 * application looking exactly as it ships. Nobody should be unable to
 * approve a leave request because a logo could not be fetched.
 */
export const BRANDING_KEY = ['tenant', 'branding']; // eslint-disable-line react-refresh/only-export-components

const BrandingContext = createContext({ branding: null, loading: false,
  refresh: () => {} });

export const BrandingProvider = ({ children }) => {
  const { isAuthenticated, isPlatformStaff } = useAuth();
  const client = useQueryClient();

  // PLATFORM STAFF ARE DELIBERATELY EXCLUDED. An operator belongs to no
  // tenant, and an operator who did would otherwise see the console wearing
  // a customer's colours -- which is precisely the confusion that makes
  // somebody suspend the wrong organization.
  const enabled = Boolean(isAuthenticated) && !isPlatformStaff;

  // ON REACT QUERY RATHER THAN A HAND-ROLLED EFFECT, for one reason beyond
  // tidiness: branding must survive a route change without being refetched
  // on every navigation, and it must be invalidatable from the settings page
  // so saving a colour repaints the application immediately. A cache keyed
  // by the session gives both; `useState` in a provider gave neither.
  const { data, isFetching } = useQuery({
    queryKey: BRANDING_KEY,
    enabled,
    queryFn: () => api.get('/tenant/branding/').then((r) => r.data),
    staleTime: 5 * 60_000,
    // NEVER RETRIED AND NEVER THROWN. The product keeps its shipped
    // appearance if this fails; nobody should be unable to approve a leave
    // request because a logo could not be fetched.
    retry: false,
  });

  const active = (enabled && data?.applicable) ? data : null;
  const primary = active?.color_primary;
  const secondary = active?.color_secondary;
  const favicon = active?.favicon;
  const displayName = active?.display_name;

  // Apply, and revert on sign-out or on a change of tenant.
  useEffect(() => {
    if (!primary && !secondary) return undefined;
    return applyTheme(paletteFor({ primary, secondary }));
  }, [primary, secondary]);

  // The tab icon, which is not a React-rendered element.
  useEffect(() => {
    const href = favicon;
    if (!href) return undefined;
    const link = document.querySelector("link[rel~='icon']");
    if (!link) return undefined;
    const shipped = link.getAttribute('href');
    link.setAttribute('href', href);
    return () => { link.setAttribute('href', shipped); };
  }, [favicon]);

  // The document title, so a browser tab and a bookmark carry the customer's
  // name rather than the platform's.
  useEffect(() => {
    const name = displayName;
    if (!name) return undefined;
    const shipped = document.title;
    document.title = `${name} — Portal`;
    return () => { document.title = shipped; };
  }, [displayName]);

  const value = useMemo(() => ({
    branding: active,
    loading: isFetching,
    refresh: () => client.invalidateQueries({ queryKey: BRANDING_KEY }),
    // Convenience readers, so a component never writes the fallback itself
    // and no screen can be the one that forgot it.
    logo: active?.logo_primary || PLATFORM_LOGO,
    name: active?.display_name || PLATFORM_NAME,
    welcome: active?.dashboard_welcome || '',
  }), [active, isFetching, client]);

  return (
    <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>
  );
};

export const useBranding = () => useContext(BrandingContext); // eslint-disable-line react-refresh/only-export-components
