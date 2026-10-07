import React, { useEffect, useRef, useState } from 'react';
import {
  Copy, ExternalLink, Mail, MessageCircle, Share2, Users,
} from 'lucide-react';

import { shareTargets } from './shareLinks';
import { useCopyLink } from './useCopyLink';

/**
 * Share a record (Phase TASK-DEEP-LINK-SHARING).
 *
 * REUSABLE BY CONSTRUCTION
 * ------------------------
 * Props are `{ kind, title, reference, url }` — a noun, a name, a reference and
 * a link. A memo, a minute, a circular, a leave application, an asset or an
 * appraisal supplies the same four and gets the same menu. Nothing here imports
 * a task service or knows a task route.
 *
 * WHY A MENU RATHER THAN FIVE BUTTONS IN THE HEADER
 * -------------------------------------------------
 * Copy Link is the action people take ninety per cent of the time, so it stays
 * a button of its own. The other four are occasional, and five icons competing
 * for the same corner is how a header stops being scannable.
 *
 * NOTHING LEAVES THE BROWSER
 * --------------------------
 * Each target is a URL the person's own client opens. No share is registered
 * anywhere, no preview is generated server-side, and no link is minted that
 * could outlive the permission check — the URL is the ordinary one, and
 * whoever opens it authenticates as themselves.
 */
const ShareMenu = ({ kind = 'Task', title, reference, url, onCopied, onFailed }) => {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const [copy] = useCopyLink();
  const targets = shareTargets({ kind, title, reference, url });

  // Close on an outside click or Escape — the two ways a person expects to
  // dismiss a menu they opened by accident.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const doCopy = async () => {
    setOpen(false);
    const ok = await copy(url);
    if (ok) onCopied?.();
    else onFailed?.(url);
  };

  const go = (href, newTab = true) => {
    setOpen(false);
    if (newTab) window.open(href, '_blank', 'noopener,noreferrer');
    else window.location.href = href;
  };

  return (
    <div className="share-menu" ref={box}>
      <button type="button" className="lr-btn" aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <Share2 size={14} /> Share
      </button>

      {open && (
        <div className="share-menu-pop" role="menu" aria-label={`Share this ${kind.toLowerCase()}`}>
          <button type="button" role="menuitem" onClick={doCopy}>
            <Copy size={14} aria-hidden="true" /> Copy link
          </button>
          <button type="button" role="menuitem" onClick={() => go(url)}>
            <ExternalLink size={14} aria-hidden="true" /> Open in new tab
          </button>
          <hr />
          {/* mailto: navigates rather than opening a tab — a new window that
              immediately hands off to a mail client leaves a blank tab behind. */}
          <button type="button" role="menuitem" onClick={() => go(targets.email, false)}>
            <Mail size={14} aria-hidden="true" /> Share by email
          </button>
          <button type="button" role="menuitem" onClick={() => go(targets.whatsapp)}>
            <MessageCircle size={14} aria-hidden="true" /> Share by WhatsApp
          </button>
          <button type="button" role="menuitem" onClick={() => go(targets.teams)}>
            <Users size={14} aria-hidden="true" /> Share by Microsoft Teams
          </button>
        </div>
      )}
    </div>
  );
};

export default ShareMenu;
