/**
 * Self-diagnosing overflow probe (Phase 46 item 1).
 *
 * Walks the rendered tree and reports every element whose content escapes its own box, or
 * whose right edge lies outside its parent's. Screenshotting a page and eyeballing it
 * finds overlaps but not their cause; this names the element and the numbers, which turns
 * "something overlaps" into "this cell is 140px and needs 197px".
 */
export function probeOverflow() {
  const findings = [];
  const root = document.getElementById('root');
  if (!root) return findings;

  for (const el of root.querySelectorAll('*')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    // .sr-only is clipped to 1px BY DESIGN - it exists to be read aloud, not seen. It
    // would otherwise dominate the report with false findings.
    if (el.classList.contains('sr-only') || el.closest('.sr-only')) continue;

    // Recharts sizes its wrapper from a ResizeObserver callback that does not fire
    // before a headless screenshot, so the wrapper keeps its unmeasured intrinsic
    // width and reports a large escapes-parent finding. It is an artifact of the
    // capture, not a layout fault, and the tell is that the overflow is IDENTICAL at
    // 1440, 768 and 390 - a real responsive overflow varies with the viewport. The
    // chart frames were also confirmed visually to sit inside their cards.
    if (el.classList.contains('recharts-wrapper') || el.closest('.recharts-wrapper')) {
      continue;
    }

    // An element that declares `text-overflow: ellipsis` with hidden overflow is
    // clipping ON PURPOSE - the ellipsis IS the design, and scrollWidth exceeding
    // clientWidth is how that design works rather than evidence it is broken. Same
    // class of false positive as .sr-only above: without this the probe reports
    // every truncated-with-ellipsis label as an overflow. The distinction is
    // declared intent, so it is safe: nothing accidentally opts in.
    if (style.textOverflow === 'ellipsis' && /hidden|clip/.test(style.overflowX)) {
      continue;
    }

    // A single-line text input scrolls its own content BY DESIGN: scrollWidth is the
    // intrinsic width of its value or placeholder, and the field clips and scrolls
    // that internally without affecting one pixel of the layout around it. Reporting
    // it as an overflow is the same false positive as text-overflow:ellipsis above.
    // (What surfaced this: two fields flagged at 337px and 303px inside a 284px
    // column at 360px, both purely because their PLACEHOLDER text is long. See the
    // Phase 100 report — the placeholders are a copy question, not a layout one.)
    if (el.tagName === 'INPUT' && !/^(checkbox|radio|file|range|color)$/.test(el.type)) {
      continue;
    }

    // Content wider than the box, with no scrolling to absorb it.
    const scrollable = /auto|scroll/.test(style.overflowX);
    if (!scrollable && el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      findings.push({
        kind: 'content-overflow',
        el: describe(el),
        need: el.scrollWidth,
        have: el.clientWidth,
        // Which descendant is actually demanding the width. Without this the report says
        // "the page needs 1264px" and leaves the search to a human; with it, it names the
        // one element to fix. A container overflowing is a symptom - this is the cause.
        because: culprit(el),
      });
    }

    // Escaping the parent's right edge (what an overlap actually looks like).
    const parent = el.parentElement;
    if (parent && parent !== root) {
      const pStyle = getComputedStyle(parent);
      if (!/auto|scroll/.test(pStyle.overflowX)) {
        const box = el.getBoundingClientRect();
        const pBox = parent.getBoundingClientRect();
        if (box.right > pBox.right + 1 && box.width > 0) {
          findings.push({
            kind: 'escapes-parent',
            el: describe(el),
            over: Math.round(box.right - pBox.right),
            parent: describe(parent),
          });
        }
      }
    }
  }

  // The page itself must never scroll sideways.
  if (document.documentElement.scrollWidth > window.innerWidth + 1) {
    findings.push({
      kind: 'page-scrolls-sideways',
      need: document.documentElement.scrollWidth,
      have: window.innerWidth,
    });
  }
  return findings;
}

/**
 * Which descendant is actually demanding the width.
 *
 * Measured, not guessed. Picking "the widest descendant" does not work: once a grid track
 * has been stretched by one child, every sibling is that wide too, and the widest element
 * is usually an innocent one that merely fills the space. So each child is hidden in turn
 * and the container re-measured — the child whose removal makes the overflow go away is
 * the one responsible — and the search then descends into it. Everything is restored
 * before returning, and this only runs for elements already known to overflow.
 */
function culprit(container) {
  let node = container;
  let guard = 0;
  for (;;) {
    if (++guard > 40) break;                       // a cycle here would hang the capture
    const children = [...node.children].filter((child) => {
      const style = getComputedStyle(child);
      return style.display !== 'none' && !/auto|scroll/.test(style.overflowX);
    });
    let next = null;
    for (const child of children) {
      const previous = child.style.display;
      child.style.display = 'none';
      const relieved = container.scrollWidth <= container.clientWidth + 1;
      child.style.display = previous;
      if (relieved) { next = child; break; }
    }
    if (!next) {
      // No single child accounts for it, which means several share the blame. Isolate
      // each in turn and report the ones that overflow on their own - naming three
      // culprits is still an answer, and "the container is wide" is not.
      const heavy = [];
      for (const child of children) {
        const hidden = children.filter((other) => other !== child);
        const saved = hidden.map((other) => other.style.display);
        hidden.forEach((other) => { other.style.display = 'none'; });
        const demand = container.scrollWidth;
        hidden.forEach((other, index) => { other.style.display = saved[index]; });
        if (demand > container.clientWidth + 1) heavy.push({ child, demand });
      }
      if (!heavy.length) break;
      heavy.sort((a, b) => b.demand - a.demand);
      return heavy.slice(0, 3)
        .map((row) => `${describe(row.child)} [needs ${row.demand}]`).join(' + ');
    }
    node = next;
  }
  return node === container ? null : describe(node);
}

function describe(el) {
  const cls = (el.className || '').toString().trim().split(/\s+/).slice(0, 3).join('.');
  // An <input> has no textContent, so the generic describe() rendered every one of
  // them as the bare word "input" — which named the defect but not WHICH field, and
  // sent the reader hunting through the form by hand. Its type and placeholder are
  // the two things that identify it.
  const text = el.tagName === 'INPUT'
    ? [el.type, el.placeholder, el.name].filter(Boolean).join(' ').slice(0, 40)
    : (el.textContent || '').trim().slice(0, 40);
  return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''}${text ? ` "${text}"` : ''}`;
}
