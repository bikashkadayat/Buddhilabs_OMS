import { describe, it, expect } from 'vitest';
import {
  applyTheme, contrastRatio, luminance, paletteFor, parseHex,
  whiteTextIsReadable,
} from './brandTheme';

/**
 * Phase S9 Part 2: the derivation that closes R28.
 *
 * The property these tests are really about is that ONE colour from a
 * customer produces a complete, self-consistent theme. The alternative --
 * asking a customer for a hover shade, a pressed shade, a tint and an rgb
 * triplet -- is five questions to get one answer wrong.
 */
describe('parseHex', () => {
  it('reads a six-digit hex', () => {
    expect(parseHex('#1D4ED8')).toEqual([29, 78, 216]);
    expect(parseHex('#1d4ed8')).toEqual([29, 78, 216]);
    expect(parseHex('  #1d4ed8 ')).toEqual([29, 78, 216]);
  });

  it('refuses everything else, so nothing reaches a stylesheet by accident', () => {
    for (const bad of ['red', '#fff', '#1d4ed', '1d4ed8', '', null, undefined,
      '#1d4ed8; background: url(javascript:alert(1))', 'var(--x)']) {
      expect(parseHex(bad), String(bad)).toBeNull();
    }
  });
});

describe('contrast', () => {
  it('matches the WCAG reference figures', () => {
    expect(contrastRatio('#FFFFFF', '#000000')).toBeCloseTo(21, 1);
    expect(contrastRatio('#FFFFFF', '#FFFFFF')).toBeCloseTo(1, 5);
  });

  it('is symmetric', () => {
    expect(contrastRatio('#274095', '#FFFFFF'))
      .toBeCloseTo(contrastRatio('#FFFFFF', '#274095'), 6);
  });

  it('knows white text is unreadable on a pale brand colour', () => {
    // The failure this guards against: a customer picks their pale corporate
    // yellow and every button in the product becomes unreadable.
    expect(whiteTextIsReadable('#FFFF00')).toBe(false);
    expect(whiteTextIsReadable('#FFE066')).toBe(false);
    expect(whiteTextIsReadable('#274095')).toBe(true);
    expect(whiteTextIsReadable('#1D4ED8')).toBe(true);
  });

  it('returns null rather than a number for an unparseable colour', () => {
    expect(contrastRatio('nonsense', '#FFFFFF')).toBeNull();
  });

  it('orders colours by luminance', () => {
    expect(luminance([255, 255, 255])).toBeGreaterThan(luminance([0, 0, 0]));
  });
});

describe('paletteFor', () => {
  it('derives the whole brand family from one colour', () => {
    const vars = paletteFor({ primary: '#1D4ED8' });
    expect(vars['--brand-blue']).toBe('#1d4ed8');
    expect(vars['--brand-blue-rgb']).toBe('29, 78, 216');
    // Lighter and darker, in the right directions.
    expect(luminance(parseHex(vars['--brand-blue-light'])))
      .toBeGreaterThan(luminance(parseHex(vars['--brand-blue'])));
    expect(luminance(parseHex(vars['--brand-blue-dark'])))
      .toBeLessThan(luminance(parseHex(vars['--brand-blue'])));
    // The tint is a tint, not a second colour.
    expect(luminance(parseHex(vars['--brand-blue-bg']))).toBeGreaterThan(0.75);
  });

  it('re-derives the borders and shadows too', () => {
    // These are brand-tinted in the stylesheet with the rgb triplet written
    // into the rgba() literals, so redefining the triplet alone does not
    // reach them -- a purple tenant would get purple buttons inside
    // blue-edged cards.
    const vars = paletteFor({ primary: '#7C3AED' });
    for (const key of ['--border', '--border-light', '--shadow-sm',
      '--shadow-md', '--shadow-lg']) {
      expect(vars[key], key).toContain('124, 58, 237');
    }
  });

  it('darkens the sidebar to the tenant hue rather than leaving it navy', () => {
    const vars = paletteFor({ primary: '#7C3AED' });
    // Dark enough to carry white text, and not the shipped #0b1120.
    expect(whiteTextIsReadable(vars['--bg-sidebar'])).toBe(true);
    expect(vars['--bg-sidebar']).not.toBe('#0b1120');
  });

  it('maps the secondary colour to the accent family', () => {
    const vars = paletteFor({ primary: '#1D4ED8', secondary: '#F59E0B' });
    expect(vars['--brand-red']).toBe('#f59e0b');
    expect(vars['--brand-red-rgb']).toBe('245, 158, 11');
  });

  it('leaves the STATUS palette alone', () => {
    // Branding changes what the product looks like, not what its signals
    // mean. An "Approved" badge in a customer's corporate purple stops
    // reading as approval.
    const vars = paletteFor({ primary: '#7C3AED', secondary: '#DC2626' });
    for (const key of ['--success', '--warning', '--danger', '--info',
      '--success-bg', '--danger-text']) {
      expect(vars).not.toHaveProperty(key);
    }
  });

  it('produces nothing at all when there is no branding', () => {
    expect(paletteFor({})).toEqual({});
    expect(paletteFor()).toEqual({});
    expect(paletteFor({ primary: 'not a colour' })).toEqual({});
  });
});

describe('applyTheme', () => {
  it('sets the properties and hands back an exact undo', () => {
    const element = document.createElement('div');
    element.style.setProperty('--brand-blue', '#274095');

    const undo = applyTheme(paletteFor({ primary: '#7C3AED' }), element);
    expect(element.style.getPropertyValue('--brand-blue')).toBe('#7c3aed');

    undo();
    // REMOVED, not reset to a literal. Setting the shipped colour back
    // explicitly would be wrong the moment the stylesheet changes -- and
    // here it would also have clobbered what the element already had.
    expect(element.style.getPropertyValue('--brand-blue')).toBe('');
  });

  it('is a no-op, not a crash, with nothing to apply', () => {
    const element = document.createElement('div');
    expect(() => applyTheme(null, element)()).not.toThrow();
    expect(() => applyTheme({}, element)()).not.toThrow();
  });
});

describe('applyTheme layering', () => {
  it('a cleanup never removes colours applied after it', async () => {
    const { applyTheme } = await import('./brandTheme');
    const el = document.createElement('div');
    const offLogin = applyTheme({ '--brand-blue': '#4f46e5' }, el);
    const offApp = applyTheme({ '--brand-blue': '#4f46e5' }, el);
    offLogin();      // the sign-in page unmounts after the app themed itself
    expect(el.style.getPropertyValue('--brand-blue')).toBe('#4f46e5');
    offApp();        // sign out
    expect(el.style.getPropertyValue('--brand-blue')).toBe('');
  });

  it('removing the top layer restores the one beneath', async () => {
    const { applyTheme } = await import('./brandTheme');
    const el = document.createElement('div');
    applyTheme({ '--brand-blue': '#111111' }, el);
    const off = applyTheme({ '--brand-blue': '#222222' }, el);
    off();
    expect(el.style.getPropertyValue('--brand-blue')).toBe('#111111');
  });
});

