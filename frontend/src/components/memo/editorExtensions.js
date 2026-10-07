import { Extension, Node, mergeAttributes } from '@tiptap/core';
import TableCell from '@tiptap/extension-table-cell';

/**
 * The four editor capabilities TipTap v2 ships no official extension for.
 *
 * Each writes an inline style the backend sanitizer explicitly permits
 * (memos/sanitizers.py ALLOWED_CSS_PROPERTIES) and base_pdf.html styles, so what
 * the author sets here survives both the save and the PDF. Adding a property to
 * one of these without adding it there produces the old, confusing failure: the
 * formatting applies in the editor and is gone after save, with no error.
 */

/** Sizes offered in the toolbar. Points, because this is a print document. */
export const FONT_SIZES = ['8pt', '9pt', '10pt', '11pt', '12pt', '14pt', '16pt', '18pt', '24pt', '30pt'];

export const FONT_FAMILIES = [
  { label: 'Default', value: '' },
  { label: 'Inter (sans)', value: 'Inter, Helvetica, Arial, sans-serif' },
  { label: 'Georgia (serif)', value: 'Georgia, "Times New Roman", serif' },
  { label: 'Times New Roman', value: '"Times New Roman", Times, serif' },
  { label: 'Arial', value: 'Arial, Helvetica, sans-serif' },
  { label: 'Courier (mono)', value: '"Courier New", Courier, monospace' },
];

export const LINE_HEIGHTS = [
  { label: 'Single', value: '1.2' },
  { label: '1.15', value: '1.15' },
  { label: '1.5', value: '1.5' },
  { label: 'Double', value: '2' },
];

/**
 * Font size as a mark attribute on TextStyle.
 *
 * TipTap v2 has extension-font-family but no font-size counterpart, so this is
 * the same shape hand-rolled: it stores `font-size` in the style attribute that
 * TextStyle already manages, rather than introducing a competing mark.
 */
export const FontSize = Extension.create({
  name: 'fontSize',

  addOptions() {
    return { types: ['textStyle'] };
  },

  addGlobalAttributes() {
    return [{
      types: this.options.types,
      attributes: {
        fontSize: {
          default: null,
          parseHTML: (element) => element.style.fontSize || null,
          renderHTML: (attributes) =>
            (attributes.fontSize ? { style: `font-size: ${attributes.fontSize}` } : {}),
        },
      },
    }];
  },

  addCommands() {
    return {
      setFontSize: (size) => ({ chain }) =>
        chain().setMark('textStyle', { fontSize: size }).run(),
      unsetFontSize: () => ({ chain }) =>
        chain().setMark('textStyle', { fontSize: null }).removeEmptyTextStyle().run(),
    };
  },
});

/**
 * Line spacing as a block attribute on paragraphs and headings.
 *
 * A node attribute rather than a mark: line height applies to a whole block, and
 * as a mark it would fragment on every partial selection and produce nested spans
 * that fight each other.
 */
export const LineHeight = Extension.create({
  name: 'lineHeight',

  addOptions() {
    return { types: ['paragraph', 'heading'] };
  },

  addGlobalAttributes() {
    return [{
      types: this.options.types,
      attributes: {
        lineHeight: {
          default: null,
          parseHTML: (element) => element.style.lineHeight || null,
          renderHTML: (attributes) =>
            (attributes.lineHeight ? { style: `line-height: ${attributes.lineHeight}` } : {}),
        },
      },
    }];
  },

  addCommands() {
    return {
      setLineHeight: (value) => ({ commands }) =>
        this.options.types.every((type) => commands.updateAttributes(type, { lineHeight: value })),
      unsetLineHeight: () => ({ commands }) =>
        this.options.types.every((type) => commands.resetAttributes(type, 'lineHeight')),
    };
  },
});

/**
 * Left indentation as a block attribute, stepped in fixed increments.
 *
 * Capped at MAX_INDENT: without a ceiling, holding the indent button walks the
 * text off the right edge of the printed page, where nothing on screen warns you
 * because the editor pane just keeps scrolling.
 */
const INDENT_STEP = 32;
const MAX_INDENT = 32 * 8;

export const Indent = Extension.create({
  name: 'indent',

  addOptions() {
    return { types: ['paragraph', 'heading', 'blockquote'] };
  },

  addGlobalAttributes() {
    return [{
      types: this.options.types,
      attributes: {
        indent: {
          default: 0,
          parseHTML: (element) => parseInt(element.style.marginLeft, 10) || 0,
          renderHTML: (attributes) =>
            (attributes.indent ? { style: `margin-left: ${attributes.indent}px` } : {}),
        },
      },
    }];
  },

  addCommands() {
    const shift = (delta) => ({ state, commands }) => {
      const { from, to } = state.selection;
      let handled = false;
      state.doc.nodesBetween(from, to, (node, pos) => {
        if (!this.options.types.includes(node.type.name)) return;
        const next = Math.min(MAX_INDENT, Math.max(0, (node.attrs.indent || 0) + delta));
        if (next !== node.attrs.indent) {
          commands.command(({ tr }) => {
            tr.setNodeMarkup(pos, undefined, { ...node.attrs, indent: next });
            return true;
          });
          handled = true;
        }
      });
      return handled;
    };
    return {
      indent: () => shift(INDENT_STEP),
      outdent: () => shift(-INDENT_STEP),
    };
  },
});

/**
 * An explicit page break: an atomic block that renders as
 * `<div class="memo-page-break">`.
 *
 * The class, not an inline `page-break-after`, is what base_pdf.html keys its
 * print rule on — and the class is on the sanitizer's ALLOWED_CLASSES list, so it
 * survives the save. On screen it draws as a labelled dashed rule so the author
 * can see where the page will actually break.
 */
export const PageBreak = Node.create({
  name: 'pageBreak',
  group: 'block',
  atom: true,
  selectable: true,

  parseHTML() {
    return [{ tag: 'div.memo-page-break' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { class: 'memo-page-break' })];
  },

  addCommands() {
    return {
      insertPageBreak: () => ({ commands }) => commands.insertContent({ type: this.name }),
    };
  },
});

/** A heavier horizontal rule for separating sections of a long memo. */
export const SectionDivider = Node.create({
  name: 'sectionDivider',
  group: 'block',
  atom: true,
  selectable: true,

  parseHTML() {
    return [{ tag: 'div.memo-divider' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { class: 'memo-divider' })];
  },

  addCommands() {
    return {
      insertSectionDivider: () => ({ commands }) => commands.insertContent({ type: this.name }),
    };
  },
});

/**
 * TableCell with a row-height attribute.
 *
 * prosemirror-tables gives column resizing for free but has no notion of row
 * height, so this stores it per cell — which is also how a browser resolves row
 * height, from the tallest cell in the row. Written as an inline `height` style,
 * which the sanitizer permits.
 */
export const ResizableTableCell = TableCell.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      rowHeight: {
        default: null,
        parseHTML: (element) => element.style.height || null,
        renderHTML: (attributes) =>
          (attributes.rowHeight ? { style: `height: ${attributes.rowHeight}` } : {}),
      },
    };
  },
});

export const ROW_HEIGHTS = [
  { label: 'Auto', value: null },
  { label: 'Short (24px)', value: '24px' },
  { label: 'Medium (40px)', value: '40px' },
  { label: 'Tall (64px)', value: '64px' },
];

/** Preset table styles, matching the classes the sanitizer and PDF both allow. */
export const TABLE_STYLES = [
  { label: 'Standard', value: 'memo-table' },
  { label: 'Bordered', value: 'memo-table memo-table-bordered' },
  { label: 'Striped rows', value: 'memo-table memo-table-striped' },
  { label: 'Financial (right-aligned)', value: 'memo-table memo-table-financial' },
  { label: 'Compact', value: 'memo-table memo-table-compact' },
];
