import { useSyncExternalStore } from 'react';

/**
 * Is the viewport below the desktop breakpoint? (Phase F)
 *
 * 1024px, matching `.sidebar`'s own breakpoint in index.css and the DESKTOP_MQ
 * Layout already uses - one number, so the rail, the tab bar and the card/row
 * switch can never disagree about what "mobile" means.
 *
 * A media query is an external store, so it is read through
 * useSyncExternalStore: React subscribes to the query's change event and reads
 * the current value on every render, so there is no window between an initial
 * state and a mount-time re-read for the viewport to change in. Tolerates
 * matchMedia's absence: jsdom and some embedded webviews do not implement it,
 * and an unguarded call would take the whole shell down.
 */
export const MOBILE_MQ = '(max-width: 1023px)';

const query = () => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(MOBILE_MQ).matches;
};

const subscribe = (onStoreChange) => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
  const mq = window.matchMedia(MOBILE_MQ);
  mq.addEventListener('change', onStoreChange);
  return () => mq.removeEventListener('change', onStoreChange);
};

export const useIsMobile = () => useSyncExternalStore(subscribe, query, () => false);

export default useIsMobile;
