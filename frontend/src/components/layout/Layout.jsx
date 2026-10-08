import GuidedTour from '../help/GuidedTour';
import React, { useState, useEffect, useCallback } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { PageNeedHelp } from '../help/NeedHelp';
import Header from './Header';
import AppSidebar from './AppSidebar';
import MobileTabBar from './MobileTabBar';
import CreateSheet from './CreateSheet';
import CommandPalette from '../search/CommandPalette';
import { useIsMobile } from '../../hooks/useIsMobile';

const DESKTOP_MQ = '(min-width: 1024px)';

const Layout = () => {
  const [open, setOpen] = useState(false);
  // Phase F. Below 1024px the bottom bar becomes primary navigation; the
  // drawer survives only for the long tail the five tabs do not cover.
  const isMobile = useIsMobile();
  const [createOpen, setCreateOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const location = useLocation();
  const close = useCallback(() => setOpen(false), []);

  // A NAVIGATION DISMISSES TRANSIENT UI — all of it, not just the drawer.
  //
  // This closed only `open`, so the search palette and the create sheet
  // survived every route change: the bottom tabs are plain NavLinks with no
  // onClick, so nothing else told Layout to put them away. The palette locks
  // body scroll while mounted, which is why a stuck overlay also froze the page.
  //
  // Keyed on `location.key`, NOT `location.pathname`. Tapping the tab you are
  // already on leaves the pathname identical and only changes the key, so a
  // pathname dependency would still strand the overlay in the commonest case of
  // all — on Home, open search, tap Home.
  //
  // Reset during render, when the key differs from the last one seen, rather
  // than in an effect: an effect let the new route paint once with the old
  // overlay still over it, then rendered everything again to close it.
  const [seenKey, setSeenKey] = useState(location.key);
  if (seenKey !== location.key) {
    setSeenKey(location.key);
    setOpen(false);
    setSearchOpen(false);
    setCreateOpen(false);
  }

  // Ctrl/Cmd-K still opens search below 1024px.
  //
  // The shortcut used to live in GlobalSearch, which the header no longer
  // renders on mobile (Phase MOBILE-NAVIGATION-CLEANUP) — so removing the
  // duplicate button would have quietly taken the keyboard route with it.
  // Most phones have no keyboard, but a tablet in a keyboard case is squarely
  // inside this breakpoint, and losing a shortcut is not what "remove the
  // duplicate icon" asked for.
  useEffect(() => {
    if (!isMobile) return undefined;
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setSearchOpen((wasOpen) => !wasOpen);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isMobile]);

  // Auto-close when the viewport crosses up to desktop, so state can't stick open.
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_MQ);
    const onChange = (e) => { if (e.matches) setOpen(false); };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // While open (mobile only): lock body scroll, ESC to close, move focus into the
  // drawer, trap Tab within it, and return focus to the hamburger on close.
  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const drawer = document.getElementById('app-sidebar');
    const focusables = () => Array.from(
      drawer?.querySelectorAll('a[href], button:not([disabled])') || []
    );
    focusables()[0]?.focus();

    const onKey = (e) => {
      if (e.key === 'Escape') { setOpen(false); return; }
      if (e.key === 'Tab') {
        const items = focusables();
        if (!items.length) return;
        const firstEl = items[0], lastEl = items[items.length - 1];
        if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);

    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKey);
      document.getElementById('menu-hamburger')?.focus();
    };
  }, [open]);

  return (
    <>
      <Header menuOpen={open} onMenu={() => setOpen((v) => !v)} />
      <GuidedTour />
      <div className="shell">
        {/*
          * Phase E1. One rail, unconditionally. The Phase E trial branched here
          * on a per-user localStorage flag; with the opt-out button gone,
          * leaving the branch would strand anyone still carrying that flag in a
          * menu with no way out of it.
          */}
        <AppSidebar open={open} onClose={close} />
        <main className="main">
          <Outlet />
          <PageNeedHelp />
        </main>
      </div>
      <div className={`drawer-backdrop ${open ? 'show' : ''}`} onClick={close} aria-hidden="true" />
      {isMobile && (
        <>
          <MobileTabBar
            onCreate={() => setCreateOpen(true)}
            onSearch={() => setSearchOpen(true)}
          />
          <CreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />
          <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
        </>
      )}
    </>
  );
};

export default Layout;
