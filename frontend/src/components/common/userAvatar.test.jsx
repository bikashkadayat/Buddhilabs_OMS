import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import UserAvatar from './UserAvatar';

/**
 * Phase OMS-USER-AVATAR-CONSISTENCY.
 *
 * The header rendered the signed-in user through `Avatar` (photo, with
 * initials as fallback); the sidebar footer drew its own
 * `<span>{user.initials}</span>`. A user with a profile photo therefore saw
 * their face in the header and "DM" in the rail — the same person, two
 * identities, on the same screen.
 *
 * The cause was three lines of duplicated wiring, not styling, so the guard is
 * against the duplication returning: nothing that represents the CURRENT user
 * may read `profile_photo` or `initials` for itself.
 */
let user = {
  full_name: 'Demo Maker', initials: 'DM', color: '#10B981',
  profile_photo: null, department: 'ENG',
};
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user, role: 'maker' }) }));

beforeEach(() => {
  user = {
    full_name: 'Demo Maker', initials: 'DM', color: '#10B981',
    profile_photo: null, department: 'ENG',
  };
});

describe('UserAvatar', () => {
  it('shows the photo when the profile has one', () => {
    user = { ...user, profile_photo: 'https://example.test/me.jpg' };
    const { container } = render(<UserAvatar size={38} />);
    const img = container.querySelector('img');
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('src')).toBe('https://example.test/me.jpg');
  });

  it('falls back to initials when there is none', () => {
    const { container } = render(<UserAvatar size={38} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('DM')).toBeInTheDocument();
  });

  it('accepts a gradient as the fallback fill', () => {
    // CSS cannot reach it — Avatar writes the background inline — so callers
    // that want one have to pass it.
    const grad = 'linear-gradient(140deg, #5b7cf0 0%, #274095 100%)';
    const { container } = render(<UserAvatar color={grad} />);
    expect(container.firstChild.style.background).toContain('linear-gradient');
  });

  it('does not claim a role colour before the user is known', () => {
    user = null;
    const { container } = render(<UserAvatar />);
    expect(screen.getByText('U')).toBeInTheDocument();
    expect(container.firstChild.style.background).toContain('153, 153, 153');
  });
});

/* ---- nobody re-wires the current user's identity by hand ---------------- */
const here = dirname(fileURLToPath(import.meta.url));
const srcRoot = join(here, '..', '..');

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  if (statSync(full).isDirectory()) return walk(full);
  return /\.jsx?$/.test(entry) && !/\.test\./.test(entry) ? [full] : [];
});

describe('one identity source', () => {
  const files = walk(srcRoot);

  it('finds the source tree at all', () => {
    // Guards against a silent empty sweep making the rule below vacuous.
    expect(files.length).toBeGreaterThan(50);
  });

  it('reads profile_photo in exactly two places', () => {
    // UserAvatar (the shared wrapper) and Profile (which edits the photo and
    // owns its own fetched copy). Anywhere else is a call site re-implementing
    // the identity, which is how the sidebar drifted.
    const readers = files
      .filter((f) => /user\?*\.profile_photo|user\.profile_photo/.test(readFileSync(f, 'utf8')))
      .map((f) => f.slice(srcRoot.length + 1));
    expect(readers).toEqual(['components/common/UserAvatar.jsx']);
  });

  it('never renders the signed-in user as bare initials', () => {
    const offenders = files.filter((f) => {
      if (f.endsWith('UserAvatar.jsx') || f.endsWith('Avatar.jsx')) return false;
      // Comments stripped first: a note explaining what the old code did is
      // not the old code, and a guard that cannot tell the difference makes
      // the fix undocumentable.
      const src = readFileSync(f, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      // `{user?.initials}` or `{user.initials}` rendered directly as content.
      return /\{\s*user\??\.initials\s*(\|\|[^}]*)?\}/.test(src);
    }).map((f) => f.slice(srcRoot.length + 1));
    expect(offenders, `render UserAvatar instead: ${offenders.join(', ')}`).toEqual([]);
  });
});

/* ---- the fallback must not outlive the URL that caused it -------------- */
describe('Avatar recovers from a bad URL', () => {
  it('retries when a new photo arrives after a failure', async () => {
    const { default: Avatar } = await import('./Avatar');
    const { rerender, container } = render(
      <Avatar photo="/media/raw/404.png" initials="DM" />,
    );
    // The image fails, exactly as the unsigned /media/ path did at login.
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('DM')).toBeInTheDocument();

    // The signed URL arrives a moment later. Before the fix `broken` was
    // sticky, so this instance stayed on initials for the rest of its life
    // while a freshly-mounted one showed the photo — the same person with two
    // identities, decided by mount order.
    rerender(<Avatar photo="/api/v1/media/?p=me.png&e=1&s=abc" initials="DM" />);
    const img = container.querySelector('img');
    expect(img, 'a new URL must get a new attempt').toBeInTheDocument();
    expect(img.getAttribute('src')).toContain('/api/v1/media/');
  });

  it('still falls back while the same bad URL is in place', () => {
    const { container } = render(<UserAvatar />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('DM')).toBeInTheDocument();
  });
});
