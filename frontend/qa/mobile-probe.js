/**
 * Phase 100 mobile-usability probe.
 *
 * The Phase 46 overflow probe answers "does anything stick out sideways". Phase 100 asks
 * two further questions that overflow cannot see:
 *
 *   1. Is every interactive control big enough to hit with a thumb (44x44 CSS px)?
 *   2. Does any control sit outside the horizontal bounds of the viewport, i.e. is it
 *      reachable only by a sideways scroll the page is not supposed to have?
 *
 * Both only matter below the tablet breakpoint, so both are skipped above 1023px — a
 * 28px icon button is a legitimate desktop affordance and reporting it there would bury
 * the mobile findings in noise.
 */
const MIN_TOUCH = 44;
/* Checkboxes and radios are replaced elements: the box IS the hit area, so a 44px
   one is a 44px square drawn on screen. 24x24 is the WCAG 2.5.8 (AA) minimum and the
   size the CSS sets; the 44px affordance is carried by the associated <label>, which
   is the thing users tap. Judging them at 44 would report a permanent, unfixable
   failure on every form in the app. */
const MIN_TOUCH_BOX = 24;
const BOX_INPUTS = new Set(['checkbox', 'radio']);

const INTERACTIVE = 'button, a[href], input:not([type=hidden]), select, textarea, '
  + '[role=button], [role=menuitem], [tabindex]:not([tabindex="-1"])';

export function probeMobile(width) {
  const findings = [];
  const root = document.getElementById('root');
  if (!root || width > 1023) return findings;

  const seen = new Map();

  for (const el of root.querySelectorAll(INTERACTIVE)) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (el.closest('.sr-only') || el.disabled) continue;
    // An anchor that flows INSIDE a line of text is a reading affordance, not a
    // control: a 16px-tall link in a sentence is what a link is supposed to be, and
    // padding it to 44px would wreck the paragraph. Judge only anchors that lay out
    // as their own box (a button, a card, a nav row) — those are tapped, not read.
    if (el.tagName === 'A' && style.display === 'inline') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;   // collapsed / not laid out

    // --- 1. Touch target ---------------------------------------------------
    // The hit area is the border box grown by any padding the PARENT contributes
    // via a larger line-box; measuring the border box is the conservative read and
    // is what a thumb actually lands on.
    const min = (el.tagName === 'INPUT' && BOX_INPUTS.has(el.type))
      ? MIN_TOUCH_BOX : MIN_TOUCH;
    if (box.height < min - 0.5 || box.width < min - 0.5) {
      // Group identical controls: a register with 40 rows of the same 28px icon
      // button is ONE defect, not 40, and listing it 40 times hides everything else.
      const key = `${signature(el)}|${Math.round(box.width)}x${Math.round(box.height)}`;
      const hit = seen.get(key);
      if (hit) { hit.count += 1; continue; }
      const finding = {
        kind: 'touch-target-too-small',
        el: describe(el),
        w: Math.round(box.width), h: Math.round(box.height),
        need: min, count: 1,
      };
      seen.set(key, finding);
      findings.push(finding);
    }

    // --- 2. Action outside the viewport ------------------------------------
    // Vertical position is NOT a defect: the page scrolls down and that is how a
    // long page works. Horizontal position is, unless an ancestor scrolls sideways
    // on purpose (a register inside .lr-table-wrap is reachable by design).
    if (box.right > width + 1 || box.left < -1) {
      if (!scrollableAncestor(el)) {
        findings.push({
          kind: 'action-outside-viewport',
          el: describe(el),
          left: Math.round(box.left), right: Math.round(box.right), viewport: width,
        });
      }
    }
  }
  return findings;
}

/**
 * Phase 100.1 Blocker 1 — a modal's action row must be reachable without scrolling.
 *
 * The Phase 100 fix pins `.lr-modal > .lr-modal-actions` (and `.memo-modal-actions`)
 * with `position: sticky; bottom: -20px`. Sticky is easy to write and easy to break:
 * an ancestor with `overflow: hidden`, a transform, or a missing height on the
 * scroll container all silently turn it back into a static row, and nothing about
 * the CSS looks different afterwards. So this measures the RESULT rather than
 * trusting the declaration — where did the footer actually end up.
 *
 * Runs at every width, not just mobile: a footer that unpins on desktop is equally a
 * defect, and the sticky rule itself is not inside a media query.
 */
export function probeModalFooters(width) {
  const findings = [];
  // Phase D1 added `.inv-modal` (the asset panel) and its `.ui-factions` action
  // row, so the register's form is measured by the same rule as every other
  // modal rather than being taken on trust.
  for (const modal of document.querySelectorAll('.lr-modal, .inv-modal')) {
    const footer = modal.querySelector(
      ':scope > .lr-modal-actions, :scope > .memo-modal-actions, '
      + ':scope > .lr-modal-foot, :scope > .ui-factions');
    const buttons = [...modal.querySelectorAll('button, [role=button]')]
      .filter((b) => !b.classList.contains('lr-modal-close'));

    // A modal with no action row is a read-only panel (BalanceCard, the leave
    // calendar day popup, the PDF preview). Nothing to pin — not a defect.
    if (!footer) {
      if (buttons.length) {
        findings.push({
          kind: 'modal-actions-unpinned',
          el: describe(modal),
          detail: `${buttons.length} action button(s) outside any pinned footer`,
        });
      }
      continue;
    }

    const style = getComputedStyle(footer);
    if (style.position !== 'sticky') {
      findings.push({
        kind: 'modal-footer-not-sticky',
        el: describe(footer), position: style.position,
      });
    }

    // The decisive check: is the footer actually ON SCREEN? `sticky` that resolves to
    // a position below the fold is the exact failure the pinning exists to prevent.
    const box = footer.getBoundingClientRect();
    if (box.bottom > window.innerHeight + 1 || box.top < -1) {
      findings.push({
        kind: 'modal-footer-off-viewport',
        el: describe(footer),
        top: Math.round(box.top), bottom: Math.round(box.bottom),
        viewport: window.innerHeight, screenWidth: width,
      });
    }
    if (box.right > width + 1 || box.left < -1) {
      findings.push({
        kind: 'modal-footer-off-viewport-x',
        el: describe(footer),
        left: Math.round(box.left), right: Math.round(box.right), viewport: width,
      });
    }
  }
  return findings;
}

/**
 * Phase 100.1 Blocker 8 — checkbox and radio accessibility.
 *
 * Phase 100 sized these at 24px rather than 44px and justified it on the grounds that
 * "the 44px affordance is carried by the associated <label>, which is what users
 * actually tap". That argument is only sound if the label really is associated, and
 * Phase 100 never checked — it was listed as a remaining risk.
 *
 * This checks it. A box is properly labelled if any ONE of these holds:
 *   - it is wrapped in a <label> (implicit association)
 *   - a <label for=...> points at its id (explicit association)
 * Both make the label text a click target that toggles the box, which is what turns a
 * 24px box into a comfortable target.
 *
 * `aria-label` is treated SEPARATELY and deliberately: it names the control for a
 * screen reader but creates NO click target, so a box whose only label is an
 * aria-label is accessible to assistive tech and still a 24px pinpoint for a thumb.
 * That distinction is the whole point of the check, so the two are reported as
 * different findings rather than both counting as "labelled".
 */
export function probeBoxLabels() {
  const findings = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('input[type=checkbox], input[type=radio]')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;

    const wrapped = Boolean(el.closest('label'));
    const explicit = Boolean(el.id
      && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
    const aria = Boolean(el.getAttribute('aria-label')
      || el.getAttribute('aria-labelledby'));

    if (wrapped || explicit) continue;

    // One finding per distinct control, not per row: a preferences table with 65
    // rows of the same unlabelled toggle is one defect to fix, not 65.
    const key = `${el.type}|${el.className}|${el.getAttribute('aria-label') ? 'aria' : 'none'}`;
    if (seen.has(key)) continue;
    seen.add(key);

    findings.push({
      kind: aria ? 'box-label-not-clickable' : 'box-has-no-label',
      el: describe(el),
      detail: aria
        ? 'named by aria-label only — screen-reader accessible, but no clickable '
          + 'label, so the 24px box is the entire touch target'
        : 'no <label> wrapper, no label[for], no aria-label',
    });
  }
  return findings;
}

/** An ancestor that scrolls sideways makes horizontal off-screen position intentional. */
function scrollableAncestor(el) {
  for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
    if (/auto|scroll/.test(getComputedStyle(n).overflowX)) return n;
  }
  return null;
}

/** Class list only — the identity that decides whether two controls are the same defect. */
function signature(el) {
  return `${el.tagName}.${(el.className || '').toString().trim()}`;
}

function describe(el) {
  const cls = (el.className || '').toString().trim().split(/\s+/).slice(0, 3).join('.');
  const text = (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40);
  return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''}${text ? ` "${text}"` : ''}`;
}
