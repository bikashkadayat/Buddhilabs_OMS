/**
 * Phase S9 Part 2: turn one tenant colour into the whole application's theme.
 *
 * WHAT R28 ACTUALLY WAS. Since Phase S6 a customer could upload a logo and
 * pick two colours, and those reached exactly one screen: the login page.
 * Once signed in, every employee of every tenant saw Nepal Internet
 * Foundation's blue -- in the sidebar, on every primary button, in every
 * border and shadow. A white-label product whose branding stops at the front
 * door is not white-label; it is a themed login screen.
 *
 * THE MECHANISM. The stylesheet already routes its brand through five custom
 * properties (`--brand-blue` and its rgb / light / dark / tint companions),
 * plus a red for destructive accents. Every component reads those rather
 * than literals. So the entire application re-themes itself if those
 * properties are redefined -- which is what `applyTheme` does, by setting
 * them on `document.documentElement` where they override the `:root` block
 * without the stylesheet being touched.
 *
 * That is deliberate: no build step, no second stylesheet, nothing to keep
 * in sync, and a tenant with no branding gets the shipped values because
 * nothing is set at all.
 *
 * WHAT IS NOT RE-THEMED, AND WHY. The status palette -- success green,
 * warning amber, danger red, info blue -- is left alone. Those colours carry
 * meaning rather than identity: an "Approved" badge rendered in a customer's
 * corporate purple stops reading as approval. Branding changes what the
 * product looks like, not what its signals mean.
 */

/** `#1D4ED8` -> `[29, 78, 216]`, or null for anything that is not a hex. */
export const parseHex = (value) => {
  const text = String(value || '').trim();
  if (!/^#[0-9a-f]{6}$/i.test(text)) return null;
  return [1, 3, 5].map((i) => parseInt(text.slice(i, i + 2), 16));
};

const toHex = (rgb) => `#${rgb.map((c) => Math.max(0, Math.min(255, Math.round(c)))
  .toString(16).padStart(2, '0')).join('')}`;

/** Move `rgb` a fraction of the way towards `target`. */
const mix = (rgb, target, amount) =>
  rgb.map((c, i) => c + (target[i] - c) * amount);

const WHITE = [255, 255, 255];
const BLACK = [0, 0, 0];

/** WCAG relative luminance. */
export const luminance = (rgb) => {
  const [r, g, b] = rgb.map((c) => c / 255);
  const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};

/** Contrast ratio between two hex colours, 1 to 21. */
export const contrastRatio = (a, b) => {
  const [x, y] = [parseHex(a), parseHex(b)];
  if (!x || !y) return null;
  const [p, q] = [luminance(x), luminance(y)];
  return (Math.max(p, q) + 0.05) / (Math.min(p, q) + 0.05);
};

/**
 * Whether white text is readable on this colour.
 *
 * SURFACED IN THE EDITOR RATHER THAN ENFORCED. A customer who picks a pale
 * yellow gets unreadable buttons, and the honest thing is to tell them so
 * before they save -- not to silently darken what they chose, which would
 * mean the colour on screen is not the colour in the field, and they would
 * keep trying to fix it.
 */
export const whiteTextIsReadable = (hex) => {
  const ratio = contrastRatio(hex, '#FFFFFF');
  return ratio !== null && ratio >= 4.5;
};

/**
 * Every custom property a tenant colour implies.
 *
 * The derived shades are generated rather than asked for: a customer knows
 * their brand colour, and making them choose a hover shade, a pressed shade
 * and a tint is five questions to get one answer wrong.
 */
export const paletteFor = ({ primary, secondary } = {}) => {
  const vars = {};
  const base = parseHex(primary);
  if (base) {
    const rgb = base.join(', ');
    vars['--brand-blue'] = toHex(base);
    vars['--brand-blue-rgb'] = rgb;
    vars['--brand-blue-light'] = toHex(mix(base, WHITE, 0.25));
    vars['--brand-blue-dark'] = toHex(mix(base, BLACK, 0.25));
    // The soft tint behind a brand glyph. 92% towards white keeps it a tint
        // rather than a second colour, at any hue.
    vars['--brand-blue-bg'] = toHex(mix(base, WHITE, 0.92));
    // Borders and shadows are brand-tinted in this stylesheet, with the rgb
    // triplet written into the rgba() literals. Redefining the triplet does
    // not reach them, so they are re-derived here -- otherwise a purple
    // tenant gets purple buttons inside blue-edged cards.
    vars['--border'] = `rgba(${rgb}, 0.12)`;
    vars['--border-light'] = `rgba(${rgb}, 0.06)`;
    vars['--border-soft'] = `rgba(${rgb}, 0.06)`;
    vars['--shadow-sm'] = `0 2px 4px rgba(${rgb}, 0.04)`;
    vars['--shadow-md'] =
      `0 8px 16px rgba(${rgb}, 0.06), 0 2px 6px rgba(${rgb}, 0.04)`;
    vars['--shadow-lg'] =
      `0 16px 32px rgba(${rgb}, 0.08), 0 4px 12px rgba(${rgb}, 0.04)`;
    // The dark sidebar: a near-black of the tenant's hue rather than the
    // shipped navy, which otherwise stays visibly NIF blue on every page.
    vars['--bg-sidebar'] = toHex(mix(mix(base, BLACK, 0.86), [11, 17, 32], 0.4));
  }
  const accentBase = parseHex(secondary);
  if (accentBase) {
    vars['--brand-red'] = toHex(accentBase);
    vars['--brand-red-rgb'] = accentBase.join(', ');
    vars['--brand-red-dark'] = toHex(mix(accentBase, BLACK, 0.15));
  }
  return vars;
};

/**
 * Apply a palette, and return a function that puts everything back.
 *
 * THE UNDO MATTERS. An operator who signs out of one tenant's workspace and
 * into another, on the same single-host deployment, must not keep the first
 * one's colours -- and `removeProperty` is the only way back to the
 * stylesheet's own value, because setting the shipped colour explicitly
 * would then be wrong the moment the stylesheet changes.
 */
/**
 * Apply a palette; return a function that takes THIS palette back off.
 *
 * LAYERED, NOT LAST-WRITER-WINS -- FOUND BY A BROWSER, ON THE FIRST SIGN-IN.
 * Two owners theme the same variables: the sign-in page (from the pre-login
 * branding) and the signed-in app (BrandingProvider). The cleanup used to
 * remove the variables outright. So when the app applied the customer's
 * colours and THEN the sign-in page unmounted, its cleanup deleted them, and
 * the workspace fell back to the platform's colours for the rest of the
 * session -- intermittently, because it depended on which request finished
 * first. Now each call is a layer per element: a cleanup removes only its
 * own layer and restores whatever is underneath.
 */
const LAYERS = new WeakMap();   // element -> Map(name -> [{ id, value }])
let nextLayer = 0;

export const applyTheme = (vars, element) => {
  const root = element || (typeof document !== 'undefined'
    ? document.documentElement : null);
  if (!root || !vars) return () => {};
  const id = ++nextLayer;
  if (!LAYERS.has(root)) LAYERS.set(root, new Map());
  const stacks = LAYERS.get(root);
  const names = Object.keys(vars);
  names.forEach((name) => {
    if (!stacks.has(name)) stacks.set(name, []);
    stacks.get(name).push({ id, value: vars[name] });
    root.style.setProperty(name, vars[name]);
  });
  return () => names.forEach((name) => {
    const stack = (stacks.get(name) || []).filter((layer) => layer.id !== id);
    stacks.set(name, stack);
    const top = stack[stack.length - 1];
    if (top) root.style.setProperty(name, top.value);
    else root.style.removeProperty(name);
  });
};

export default paletteFor;
