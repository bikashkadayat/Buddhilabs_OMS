import { useEffect } from 'react';

/**
 * Exit protection (Phase 111.11).
 *
 * Two different exits need two different mechanisms, and neither covers the
 * other:
 *
 *   beforeunload   refresh, tab close, window close, navigating to another
 *                  site. The browser owns the wording; a page cannot customise
 *                  it, and Chrome ignores any string you return.
 *   link intercept in-app navigation — a sidebar click, a Back-to-list link.
 *                  These never reach beforeunload, because the document never
 *                  unloads; React Router swaps the view in place.
 *
 * WHY NOT useBlocker
 * React Router 7 ships `useBlocker`, which is the obvious tool and is what the
 * design sketch proposed. It throws "useBlocker must be used within a data
 * router" under `<BrowserRouter>`, which is what this app uses. Migrating the
 * whole app to `createBrowserRouter` to gain a confirm dialog would be a large
 * routing change for a small feature, so the guard intercepts the click instead.
 *
 * KNOWN LIMIT
 * Programmatic `navigate()` calls (a button rather than a link) and the browser
 * Back button are not intercepted. Back cannot be blocked reliably without a
 * data router, and faking it by re-pushing history makes the URL flicker. The
 * three cases named in the requirement — refresh, leaving the page, closing the
 * tab — are all covered.
 *
 * @param {boolean} when     block only while there is something to lose
 * @param {string}  message  confirm() text for the in-app case
 */
export const useExitGuard = (when, message = 'You have unsaved changes. Leave anyway?') => {
  // 1. Refresh / close / navigate away from the site.
  useEffect(() => {
    if (!when) return undefined;
    const handler = (event) => {
      event.preventDefault();
      // Chrome shows its own text and ignores this, but Safari still needs
      // returnValue set for the dialog to appear at all.
      event.returnValue = '';
      return '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [when]);

  // 2. In-app links. Capture phase, so the confirm runs before React Router's
  //    own click handler gets the event and starts the transition.
  useEffect(() => {
    if (!when || typeof document === 'undefined') return undefined;

    const handler = (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      // Ctrl/Cmd/Shift-click opens elsewhere and leaves this page intact.
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const anchor = event.target?.closest?.('a[href]');
      if (!anchor) return;
      if (anchor.target && anchor.target !== '_self') return;
      if (anchor.hasAttribute('download')) return;

      const href = anchor.getAttribute('href');
      if (!href || href.startsWith('#')) return;

      // Same-page links are not an exit.
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname) return;

      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };

    document.addEventListener('click', handler, true);
    return () => document.removeEventListener('click', handler, true);
  }, [when, message]);
};

export default useExitGuard;
