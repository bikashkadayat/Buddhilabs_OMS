import { useCallback, useState } from 'react';

/**
 * Copy text to the clipboard, with the fallback that makes it work off HTTPS
 * (Phase TASK-DEEP-LINK-SHARING).
 *
 * `navigator.clipboard` exists only in a SECURE CONTEXT — https, or localhost.
 * An internal deployment reached over plain http on the LAN has no clipboard
 * API at all, and a Copy button that silently does nothing there is worse than
 * no button. The textarea-and-execCommand path is deprecated and still the only
 * thing that works in that case.
 *
 * Returns [copy, state] where state is 'idle' | 'done' | 'failed', so the caller
 * can show a toast AND fall back to showing the URL for manual copying when
 * even that fails (a locked-down browser, or a permissions policy).
 */
export const useCopyLink = () => {
  const [state, setState] = useState('idle');

  const copy = useCallback(async (text) => {
    const done = () => { setState('done'); return true; };
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return done();
      }
    } catch {
      // Fall through: a rejected permission is not a reason to give up yet.
    }
    try {
      const area = document.createElement('textarea');
      area.value = text;
      // Off-screen rather than hidden: `display:none` is not selectable, and
      // a visible flash of the URL is exactly what we are avoiding.
      area.setAttribute('readonly', '');
      area.style.cssText = 'position:fixed;top:-1000px;opacity:0;';
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(area);
      if (ok) return done();
    } catch {
      // Reported below.
    }
    setState('failed');
    return false;
  }, []);

  return [copy, state];
};

export default useCopyLink;
