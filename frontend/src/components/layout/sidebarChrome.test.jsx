import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Phase OMS-NAVIGATION-EXECUTIVE-UI-UPGRADE.
 *
 * The rail read as a generic admin template: 12px-padded rows, an active state
 * that was a flat wash plus a border-left, a bare "GO TO" floating in a gap,
 * and an identity block sitting loose above a full-width seam.
 *
 * These assert the STRUCTURE of the replacement rather than exact colours —
 * colours are meant to be tuned, but a row losing its height, an active state
 * losing a cue, or the pill going back to being the row's own background are
 * regressions. Read from the stylesheet because none of this is observable in
 * jsdom, which computes no cascade for pseudo-elements.
 */
const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, '..', '..', 'index.css'), 'utf8');

/** The body of the first rule whose selector matches exactly. */
const rule = (selector) => {
  const at = css.indexOf(`\n${selector} {`);
  expect(at, `no rule for "${selector}"`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf('}', at));
};

describe('sidebar row rhythm', () => {
  it('gives every row a 48px floor and a single spacing rule', () => {
    const item = rule('.sb-item');
    expect(item).toMatch(/min-height:\s*48px/);
    expect(item).toMatch(/gap:\s*14px/);
    // The height must come from min-height, not vertical padding: a wrapping
    // label has to grow the row rather than break the column's rhythm.
    expect(item).not.toMatch(/padding:\s*1[2-9]px/);
  });

  it('declares .sb-item exactly once, not once per phase', () => {
    // Two rules had accumulated, so geometry came from one and transitions
    // from the other — which is how `transition: all 0.2s` survived.
    const count = (css.match(/\n\.sb-item \{/g) || []).length;
    expect(count, `.sb-item is declared ${count} times`).toBe(1);
    expect(rule('.sb-item')).not.toMatch(/transition:\s*all/);
  });
});

describe('active state', () => {
  it('carries the pill, the accent bar, the glyph and the label', () => {
    const pill = rule('.sb-item::before');
    expect(pill, 'pill is not a gradient').toMatch(/linear-gradient/);
    expect(pill, 'pill has no glass rim').toMatch(/inset 0 1px 0/);
    expect(pill, 'pill has no depth').toMatch(/box-shadow/);
    expect(pill).toMatch(/backdrop-filter:\s*blur/);
    // Prefixed as well: the unprefixed property alone drops the glass on
    // Safari, which is where it is most visible.
    expect(pill).toMatch(/-webkit-backdrop-filter/);

    expect(rule('.sb-item::after'), 'no accent bar').toMatch(/width:\s*3px/);
    expect(css).toMatch(/\.sb-item\.on::before \{[^}]*opacity:\s*1/);
    expect(css).toMatch(/\.sb-item\.on::after \{[^}]*opacity:\s*1/);
    expect(css).toMatch(/\.sb-item\.on \.sb-ico \{[^}]*drop-shadow/);
    expect(rule('.sb-item.on')).toMatch(/font-weight:\s*600/);
  });

  it('does not paint the pill as the row background', () => {
    // The badge and label must paint above it, and a background repaint is
    // the expensive way to animate a gradient.
    expect(rule('.sb-item')).not.toMatch(/background/);
  });

  it('rests fully off, so the active row is unambiguous', () => {
    expect(rule('.sb-item::before')).toMatch(/opacity:\s*0;/);
    expect(rule('.sb-item::after')).toMatch(/opacity:\s*0;/);
  });
});

describe('grouping and footer', () => {
  it('gives the section header a divider rather than a bare word', () => {
    expect(rule('.sb-grp::before')).toMatch(/linear-gradient/);
    // The first group has nothing above it to divide from.
    expect(css).toMatch(/\.sb-items > \.sb-grp:first-child::before \{[^}]*display:\s*none/);
  });

  it('separates the footer with a fading rule, not a full-width seam', () => {
    const divider = rule('.sb-foot::before');
    expect(divider).toMatch(/linear-gradient/);
    expect(divider).toMatch(/transparent 0%/);
  });

  it('renders the identity block as a card', () => {
    const card = rule('.sb-foot-who');
    // 14px, up from 12 with the card's padding (Phase
    // OMS-SIDEBAR-FOOTER-ENTERPRISE). Asserted as a range rather than a
    // literal: the exact radius is a design choice that may move, but a footer
    // card with square-ish corners is a regression.
    const radius = Number(card.match(/border-radius:\s*(\d+)px/)[1]);
    expect(radius).toBeGreaterThanOrEqual(12);
    expect(card).toMatch(/backdrop-filter/);
    expect(card).toMatch(/box-shadow:\s*inset/);
    // The lit rim along the top edge is what gives the glass an edge against
    // a near-black rail; without it the card is an invisible flat panel.
    expect(card, 'card has no lit rim').toMatch(/inset 0 1px 0/);
    // The avatar's fallback fill moved out of CSS in Phase
    // OMS-USER-AVATAR-CONSISTENCY: the footer now renders the shared
    // UserAvatar, which writes its background inline, so a stylesheet cannot
    // reach it and the gradient is passed as a prop instead. The intent is
    // unchanged — the tile must not be a flat block of brand blue — so the
    // assertion follows it to where it now lives.
    const sidebar = readFileSync(join(here, 'AppSidebar.jsx'), 'utf8');
    expect(sidebar, 'avatar is a flat fill').toMatch(/AVATAR_GRADIENT\s*=\s*'linear-gradient/);
    expect(sidebar, 'footer does not use the shared avatar')
      .toMatch(/<UserAvatar[^>]*className="sb-foot-av-img"/);
    // …and the ring, which Avatar does not write inline, is still CSS.
    expect(rule('.sb-foot-av-img')).toMatch(/box-shadow/);
  });
});

describe('motion', () => {
  it('keeps every transition inside 150-200ms', () => {
    const region = css.slice(css.indexOf('\n.sb-item {'), css.indexOf('.sb-n {'));
    const durations = [...region.matchAll(/(\d+)ms/g)].map((m) => Number(m[1]));
    expect(durations.length).toBeGreaterThan(3);
    for (const ms of durations) expect(ms).toBeGreaterThanOrEqual(150);
    for (const ms of durations) expect(ms).toBeLessThanOrEqual(200);
  });

  it('turns motion off when the viewer asks for that', () => {
    const at = css.indexOf('@media (prefers-reduced-motion: reduce)',
      css.indexOf('\n.sb-item {'));
    expect(at).toBeGreaterThan(-1);
    const block = css.slice(at, at + 400);
    expect(block).toMatch(/transition:\s*none/);
    expect(block).toMatch(/transform:\s*none/);
  });
});
