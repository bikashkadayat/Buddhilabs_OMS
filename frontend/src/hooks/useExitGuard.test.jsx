import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import useExitGuard from './useExitGuard';

/**
 * These exist because the first implementation used React Router's `useBlocker`,
 * which throws under `<BrowserRouter>` — the router this app actually uses. That
 * would have crashed every create and edit form in production. The suite now
 * mounts the guard the way the app mounts it, with no data router in sight.
 */
const Guarded = ({ when = true, message }) => {
  useExitGuard(when, message);
  return (
    <div>
      <a href="/memos">sidebar link</a>
      <a href="/memos/create#section">same page anchor</a>
      <a href="https://example.com/x">external</a>
      <a href="/report.pdf" download="report.pdf">download</a>
      <a href="/memos" target="_blank" rel="noreferrer">new tab</a>
    </div>
  );
};

let confirmSpy;

beforeEach(() => {
  window.history.replaceState({}, '', '/memos/create');
  confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(() => confirmSpy.mockRestore());

/**
 * The guard runs in the CAPTURE phase and calls stopPropagation when it blocks,
 * so "did the click reach the bubble phase" is the cleanest signal. It also lets
 * the test swallow the click before jsdom tries to perform a real navigation it
 * cannot implement.
 */
const clickAndSeeIfBlocked = (name) => {
  let reached = false;
  const sink = (event) => { reached = true; event.preventDefault(); };
  document.addEventListener('click', sink);
  fireEvent.click(screen.getByText(name));
  document.removeEventListener('click', sink);
  return !reached;
};

describe('useExitGuard — mounting', () => {
  it('does not throw without a data router', () => {
    /* The regression that would have broken every form. */
    expect(() => render(<Guarded />)).not.toThrow();
  });
});

describe('useExitGuard — leaving the site', () => {
  it('warns on refresh or tab close when there is unsaved work', () => {
    render(<Guarded when />);
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('stays silent when there is nothing to lose', () => {
    render(<Guarded when={false} />);
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });
});

describe('useExitGuard — in-app navigation', () => {
  it('blocks a sidebar link when the user declines', () => {
    /* The exact gap the old Memo Cancel dialog left open. */
    render(<Guarded when />);
    expect(clickAndSeeIfBlocked('sidebar link')).toBe(true);
    expect(confirmSpy).toHaveBeenCalled();
  });

  it('lets the navigation through when the user accepts', () => {
    confirmSpy.mockReturnValue(true);
    render(<Guarded when />);
    expect(clickAndSeeIfBlocked('sidebar link')).toBe(false);
  });

  it('does not prompt when there is nothing to lose', () => {
    render(<Guarded when={false} />);
    clickAndSeeIfBlocked('sidebar link');
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it.each([
    ['same page anchor'],
    ['external'],
    ['download'],
    ['new tab'],
  ])('does not prompt for a %s link', (name) => {
    render(<Guarded when />);
    clickAndSeeIfBlocked(name);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('does not prompt on a modified click, which opens elsewhere', () => {
    render(<Guarded when />);
    fireEvent.click(screen.getByText('sidebar link'), { metaKey: true });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('uses the caller’s wording', () => {
    render(<Guarded when message="Your memo is not saved. Leave?" />);
    clickAndSeeIfBlocked('sidebar link');
    expect(confirmSpy).toHaveBeenCalledWith('Your memo is not saved. Leave?');
  });

  it('stops listening once the work is saved', () => {
    const { rerender } = render(<Guarded when />);
    rerender(<Guarded when={false} />);
    clickAndSeeIfBlocked('sidebar link');
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
