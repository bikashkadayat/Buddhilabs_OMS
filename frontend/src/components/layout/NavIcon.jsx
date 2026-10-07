import React from 'react';
import { NAV_ICONS } from './navIcons';

/**
 * One navigation glyph (Phase OMS-NAVIGATION-ICON-CONSISTENCY).
 *
 * Size and stroke are set HERE, not by the caller: the whole point of the
 * registry is that a sidebar entry, a tab-bar entry and a command-palette row
 * cannot end up different weights. `size` is overridable only because the
 * mobile tab bar is genuinely a larger touch target — a layout decision, not a
 * styling one.
 *
 * An unknown name falls back to a neutral glyph rather than rendering nothing,
 * so a typo shows as the wrong icon instead of a hole in the menu.
 *
 * WEIGHT (Phase OMS-NAVIGATION-PREMIUM-ICON-UPGRADE)
 * -------------------------------------------------
 * 22px at stroke 2.25, raised from 18/2. At the old weight a column of
 * outline glyphs read as grey texture beside the label rather than as
 * distinct marks — the icons were present but carried no visual authority,
 * which is what "placeholder-like" describes. The two numbers move together:
 * a heavier stroke on a small glyph closes its interior counters and turns it
 * into a blob, so growing the box is what makes the extra weight legible.
 */
const NavIcon = ({ name, size = 22, className = '' }) => {
  const Glyph = NAV_ICONS[name] || NAV_ICONS.folder;
  return <Glyph size={size} strokeWidth={2.25} className={className} aria-hidden="true" />;
};

export { NavIcon };
export default NavIcon;
