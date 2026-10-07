import React, { Suspense, lazy } from 'react';
import createDOMPurify from 'dompurify';

// The editable TipTap surface is code-split so ProseMirror/TipTap only loads when
// a memo is actually edited, not on every read-only render (L2). That matters more
// now than it did: the enterprise editor pulls tables, images and a dozen
// formatting extensions with it.
const TiptapEditor = lazy(() => import('./TiptapEditor'));

/**
 * Read-path sanitization. Defence in depth — the backend sanitizes on write and
 * is the authority — but this list must MIRROR memos/sanitizers.py, not lag it.
 *
 * When the two disagree the failure is silent and baffling: content saves
 * successfully, the API returns it intact, and the browser then strips it on
 * render, so the author sees their table vanish only after a reload. Anything
 * added to the backend allowlist belongs here in the same change.
 */
const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'span', 'div',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'strike',
    'sub', 'sup', 'code', 'pre',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'hr',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tfoot', 'caption', 'colgroup', 'col', 'tr', 'th', 'td',
    'a', 'img',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'target', 'rel',
    'src', 'alt', 'width', 'height',
    'colspan', 'rowspan', 'colwidth', 'align', 'valign', 'scope', 'span',
    'class', 'style', 'data-type', 'data-checked',
  ],
};

/**
 * Images must be raster data: URIs — never SVG, which is a script container, and
 * never remote.
 *
 * Enforced with a hook rather than with `ALLOWED_URI_REGEXP`, for two reasons
 * found the hard way:
 *
 *  1. DOMPurify applies ALLOWED_URI_REGEXP to EVERY allowed attribute, not only
 *     to URI-bearing ones. A tightened pattern therefore rejected `colspan="2"`
 *     and `colwidth="180"` for not looking like URLs, silently flattening every
 *     table on render.
 *  2. DOMPurify separately permits ANY `data:` URI on <img src> through its own
 *     DATA_URI_TAGS branch, which bypasses ALLOWED_URI_REGEXP altogether — so an
 *     SVG data URI got through even with the tightened pattern in place.
 *
 * The hook runs after attribute sanitization and has the final say on both.
 */
const RASTER_DATA_URI = /^data:image\/(png|jpe?g|gif|webp);base64,/i;

// A dedicated instance, so these hooks apply to memo bodies only. Registering
// them on the shared default instance would silently change how every other part
// of the app sanitizes.
const purifier = createDOMPurify(window);

purifier.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'IMG') {
    const src = node.getAttribute('src') || '';
    // Removed outright rather than stripped of its src: a bare <img> renders as a
    // broken-image icon, where the right outcome is nothing at all.
    if (!RASTER_DATA_URI.test(src.trim())) node.remove();
  }
  if (node.tagName === 'A' && /^\s*data:/i.test(node.getAttribute('href') || '')) {
    node.removeAttribute('href');
  }
});

const sanitize = (html) => purifier.sanitize(html || '', PURIFY_CONFIG);

const FallbackTextarea = ({ value, onChange, placeholder, minHeight, ariaLabel }) => (
  <textarea
    className="lr-field" style={{ minHeight, width: '100%' }}
    defaultValue={value} placeholder={placeholder}
    onChange={(e) => onChange?.(e.target.value)}
    aria-label={ariaLabel || 'Memo body'}
  />
);

/**
 * Rich-text memo body. readOnly renders sanitized stored HTML (no editor
 * dependency); editable lazy-loads the TipTap surface with a textarea fallback.
 *
 * The read-only container carries `memo-body`, the same class base_pdf.html
 * styles, so a memo looks the same on screen as it does on paper.
 *
 * `ariaLabel` names the textbox for assistive tech. It defaults to "Memo body"
 * for the memo callers; the task form passes "Task description", because a
 * screen reader announcing "Memo body" on a task page is a wrong answer.
 *
 * @param {{value?:string, onChange?:(html:string)=>void, placeholder?:string,
 *          readOnly?:boolean, minHeight?:number, ariaLabel?:string}} props
 */
const RichTextEditor = ({
  value = '', onChange, placeholder = 'Write the memo body…', readOnly = false,
  minHeight = 320, ariaLabel,
}) => {
  if (readOnly) {
    return (
      <div
        className="lr-richtext-view memo-body"
        dangerouslySetInnerHTML={{ __html: sanitize(value) || '<p><em>No content.</em></p>' }}
      />
    );
  }

  return (
    <Suspense fallback={<FallbackTextarea value={value} onChange={onChange} placeholder={placeholder} minHeight={minHeight} ariaLabel={ariaLabel} />}>
      <TiptapEditor value={value} onChange={onChange} placeholder={placeholder} minHeight={minHeight} ariaLabel={ariaLabel} />
    </Suspense>
  );
};

export default RichTextEditor;
