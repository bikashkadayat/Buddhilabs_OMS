import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Underline from '@tiptap/extension-underline';
import Subscript from '@tiptap/extension-subscript';
import Superscript from '@tiptap/extension-superscript';
import TextAlign from '@tiptap/extension-text-align';
import TextStyle from '@tiptap/extension-text-style';
import Color from '@tiptap/extension-color';
import Highlight from '@tiptap/extension-highlight';
import FontFamily from '@tiptap/extension-font-family';
import Image from '@tiptap/extension-image';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import Table from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableHeader from '@tiptap/extension-table-header';
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, Bold, CheckSquare,
  ChevronDown, Columns3, Highlighter, Image as ImageIcon, Indent as IndentIcon,
  Italic, Link as LinkIcon, List, ListOrdered, Minus, Outdent, Palette, Quote,
  Rows3, Scissors, SeparatorHorizontal, Strikethrough, Subscript as SubIcon,
  Superscript as SupIcon, Table as TableIcon, Trash2, Underline as UnderlineIcon,
} from 'lucide-react';
import {
  FONT_FAMILIES, FONT_SIZES, FontSize, Indent, LINE_HEIGHTS, LineHeight,
  PageBreak, ROW_HEIGHTS, ResizableTableCell, SectionDivider, TABLE_STYLES,
} from './editorExtensions';

// Images are embedded as data: URIs, which is the only form the backend
// sanitizer accepts — a remote <img src> would make the PDF renderer fetch an
// arbitrary URL from inside our network. That means every image is inlined in the
// memo body, so it has to be small: anything larger is downscaled, and anything
// still over the ceiling after that is refused with a reason.
const MAX_IMAGE_PIXELS = 1400;
const MAX_IMAGE_BYTES = 1_500_000;

const TEXT_COLORS = ['#111827', '#374151', '#b91c1c', '#c2410c', '#a16207',
  '#15803d', '#0f766e', '#1d4ed8', '#6d28d9', '#be185d'];
const HIGHLIGHTS = ['#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#e9d5ff', '#fed7aa'];

/** Toolbar button. `active` drives aria-pressed so state is announced, not just drawn. */
const Btn = ({ active, disabled, onClick, title, children }) => (
  <button type="button" title={title} aria-label={title} aria-pressed={!!active}
    disabled={disabled} className={`lr-rt-btn ${active ? 'on' : ''}`}
    onClick={onClick}>{children}</button>
);

/** A toolbar dropdown that closes on outside click and on Escape. */
const Menu = ({ label, title, icon, children, width = 200 }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="lr-rt-menu" ref={ref}>
      <button type="button" className={`lr-rt-btn is-menu ${open ? 'on' : ''}`}
        title={title} aria-label={title} aria-expanded={open} aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}>
        {icon}{label && <span className="lr-rt-menu-label">{label}</span>}
        <ChevronDown size={11} aria-hidden="true" />
      </button>
      {open && (
        <div className="lr-rt-menu-pop" style={{ width }} role="menu"
          onClick={() => setOpen(false)}>{children}</div>
      )}
    </div>
  );
};

const MenuItem = ({ onClick, children, active }) => (
  <button type="button" role="menuitem" className={`lr-rt-menu-item ${active ? 'on' : ''}`}
    onClick={onClick}>{children}</button>
);

const Swatches = ({ colors, onPick, onClear, clearLabel }) => (
  <>
    <div className="lr-rt-swatches">
      {colors.map((c) => (
        <button key={c} type="button" className="lr-rt-swatch" style={{ background: c }}
          title={c} aria-label={c} onClick={() => onPick(c)} />
      ))}
    </div>
    <MenuItem onClick={onClear}>{clearLabel}</MenuItem>
  </>
);

/**
 * The enterprise memo editor (Phase 13).
 *
 * Split into its own module so TipTap/ProseMirror — now a substantially larger
 * dependency than it was — is lazy-loaded only when someone actually edits a
 * memo. The read-only view in RichTextEditor pulls none of it.
 *
 * Every capability here has a matching entry in the backend sanitizer's
 * allowlist; that module is the gate, and a toolbar button whose output it strips
 * is worse than no button at all, because the author watches their work vanish on
 * save with no error.
 */
const TiptapEditor = ({ value = '', onChange, placeholder = 'Write the memo body…',
  minHeight = 320, ariaLabel }) => {
  const [imageError, setImageError] = useState(null);
  const fileRef = useRef(null);

  const editor = useEditor({
    /* The contenteditable surface is a `textbox` in the accessibility tree, and
       without this it is an UNNAMED one — a screen reader announces "edit text" with
       no indication of what it edits. The <textarea> fallback further down already
       carried `aria-label="Memo body"`; the real editor did not, which is the one
       people actually use. Found by reading the ARIA tree, not the DOM: the DOM looks
       perfectly reasonable here. */
    editorProps: {
      attributes: { 'aria-label': ariaLabel || 'Memo body', role: 'textbox' },
    },
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3, 4] } }),
      Link.configure({ openOnClick: false, autolink: true }),
      Underline, Subscript, Superscript,
      TextStyle, Color, FontFamily, FontSize, LineHeight, Indent,
      Highlight.configure({ multicolor: true }),
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      Image.configure({ inline: false, allowBase64: true }),
      TaskList, TaskItem.configure({ nested: true }),
      // `resizable` is what turns on prosemirror-tables' column resizing, which
      // writes the widths into <td colwidth> — the attribute the sanitizer keeps
      // and the PDF's `table-layout: fixed` then honours.
      Table.configure({ resizable: true, lastColumnResizable: true, allowTableNodeSelection: true }),
      TableRow, TableHeader, ResizableTableCell,
      PageBreak, SectionDivider,
    ],
    content: value || '',
    immediatelyRender: false,
    onUpdate: ({ editor: ed }) => onChange?.(ed.getHTML()),
  });

  // Sync external value changes (loading a template) without cursor jumps.
  useEffect(() => {
    if (editor && value !== editor.getHTML()) {
      editor.commands.setContent(value || '', false);
    }
  }, [value, editor]);

  /**
   * Downscale to a data URI. Reading the file straight to base64 would inline a
   * 6 MB phone photo into the memo body, which then travels in every list
   * response and every PDF render.
   */
  const addImage = useCallback((file) => {
    setImageError(null);
    if (!file) return;
    if (!/^image\/(png|jpe?g|gif|webp)$/i.test(file.type)) {
      setImageError('Images must be PNG, JPEG, GIF or WebP.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const img = new window.Image();
      img.onload = () => {
        const scale = Math.min(1, MAX_IMAGE_PIXELS / Math.max(img.width, img.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        const isPng = /png/i.test(file.type);
        const uri = canvas.toDataURL(isPng ? 'image/png' : 'image/jpeg', 0.85);
        if (uri.length > MAX_IMAGE_BYTES) {
          setImageError('That image is too large even after resizing. Attach it to the memo instead.');
          return;
        }
        editor.chain().focus().setImage({ src: uri, alt: file.name }).run();
      };
      img.onerror = () => setImageError('That file could not be read as an image.');
      img.src = reader.result;
    };
    reader.onerror = () => setImageError('That file could not be read.');
    reader.readAsDataURL(file);
  }, [editor]);

  if (!editor) {
    return (
      <textarea
        className="lr-field" style={{ minHeight, width: '100%' }}
        defaultValue={value} placeholder={placeholder}
        onChange={(e) => onChange?.(e.target.value)}
        aria-label={ariaLabel || 'Memo body'}
      />
    );
  }

  const chain = () => editor.chain().focus();
  const setLink = () => {
    const url = window.prompt('Link URL:', editor.getAttributes('link').href || 'https://');
    if (url === null) return;
    if (url === '') chain().unsetLink().run();
    else chain().extendMarkRange('link').setLink({ href: url }).run();
  };
  const setTableClass = (value_) => {
    // Applied with a DOM attribute rather than a node attribute: the preset is
    // purely presentational, and the class names are the ones both the sanitizer
    // and the PDF already know.
    const { state, view } = editor;
    const { $from } = state.selection;
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      if ($from.node(depth).type.name === 'table') {
        const pos = $from.before(depth);
        view.dispatch(state.tr.setNodeMarkup(pos, undefined, {
          ...$from.node(depth).attrs, class: value_,
        }));
        return;
      }
    }
  };
  const inTable = editor.isActive('table');

  return (
    <div className="lr-richtext is-enterprise">
      <div className="lr-rt-toolbar" role="toolbar" aria-label="Formatting">
        {/* --- text --- */}
        <div className="lr-rt-group">
          <Btn active={editor.isActive('bold')} onClick={() => chain().toggleBold().run()} title="Bold"><Bold size={15} /></Btn>
          <Btn active={editor.isActive('italic')} onClick={() => chain().toggleItalic().run()} title="Italic"><Italic size={15} /></Btn>
          <Btn active={editor.isActive('underline')} onClick={() => chain().toggleUnderline().run()} title="Underline"><UnderlineIcon size={15} /></Btn>
          <Btn active={editor.isActive('strike')} onClick={() => chain().toggleStrike().run()} title="Strikethrough"><Strikethrough size={15} /></Btn>
          <Btn active={editor.isActive('subscript')} onClick={() => chain().toggleSubscript().run()} title="Subscript"><SubIcon size={15} /></Btn>
          <Btn active={editor.isActive('superscript')} onClick={() => chain().toggleSuperscript().run()} title="Superscript"><SupIcon size={15} /></Btn>
        </div>

        {/* --- typeface --- */}
        <div className="lr-rt-group">
          <Menu title="Paragraph style" label="Style" width={190}>
            <MenuItem active={editor.isActive('paragraph')} onClick={() => chain().setParagraph().run()}>Body text</MenuItem>
            {[1, 2, 3, 4].map((level) => (
              <MenuItem key={level} active={editor.isActive('heading', { level })}
                onClick={() => chain().toggleHeading({ level }).run()}>Heading {level}</MenuItem>
            ))}
          </Menu>
          <Menu title="Font family" label="Font" width={230}>
            {FONT_FAMILIES.map((f) => (
              <MenuItem key={f.label}
                onClick={() => (f.value ? chain().setFontFamily(f.value).run() : chain().unsetFontFamily().run())}>
                <span style={{ fontFamily: f.value || 'inherit' }}>{f.label}</span>
              </MenuItem>
            ))}
          </Menu>
          <Menu title="Font size" label="Size" width={120}>
            {FONT_SIZES.map((size) => (
              <MenuItem key={size} onClick={() => chain().setFontSize(size).run()}>{size}</MenuItem>
            ))}
            <MenuItem onClick={() => chain().unsetFontSize().run()}>Default</MenuItem>
          </Menu>
          <Menu title="Text colour" icon={<Palette size={15} />} width={190}>
            <Swatches colors={TEXT_COLORS} onPick={(c) => chain().setColor(c).run()}
              onClear={() => chain().unsetColor().run()} clearLabel="Automatic" />
          </Menu>
          <Menu title="Highlight" icon={<Highlighter size={15} />} width={190}>
            <Swatches colors={HIGHLIGHTS} onPick={(c) => chain().toggleHighlight({ color: c }).run()}
              onClear={() => chain().unsetHighlight().run()} clearLabel="No highlight" />
          </Menu>
        </div>

        {/* --- paragraph --- */}
        <div className="lr-rt-group">
          <Btn active={editor.isActive({ textAlign: 'left' })} onClick={() => chain().setTextAlign('left').run()} title="Align left"><AlignLeft size={15} /></Btn>
          <Btn active={editor.isActive({ textAlign: 'center' })} onClick={() => chain().setTextAlign('center').run()} title="Align centre"><AlignCenter size={15} /></Btn>
          <Btn active={editor.isActive({ textAlign: 'right' })} onClick={() => chain().setTextAlign('right').run()} title="Align right"><AlignRight size={15} /></Btn>
          <Btn active={editor.isActive({ textAlign: 'justify' })} onClick={() => chain().setTextAlign('justify').run()} title="Justify"><AlignJustify size={15} /></Btn>
          <Menu title="Line spacing" label="Spacing" width={150}>
            {LINE_HEIGHTS.map((h) => (
              <MenuItem key={h.value} onClick={() => chain().setLineHeight(h.value).run()}>{h.label}</MenuItem>
            ))}
            <MenuItem onClick={() => chain().unsetLineHeight().run()}>Default</MenuItem>
          </Menu>
          <Btn onClick={() => chain().indent().run()} title="Increase indent"><IndentIcon size={15} /></Btn>
          <Btn onClick={() => chain().outdent().run()} title="Decrease indent"><Outdent size={15} /></Btn>
        </div>

        {/* --- blocks --- */}
        <div className="lr-rt-group">
          <Btn active={editor.isActive('bulletList')} onClick={() => chain().toggleBulletList().run()} title="Bullet list"><List size={15} /></Btn>
          <Btn active={editor.isActive('orderedList')} onClick={() => chain().toggleOrderedList().run()} title="Numbered list"><ListOrdered size={15} /></Btn>
          <Btn active={editor.isActive('taskList')} onClick={() => chain().toggleTaskList().run()} title="Checklist"><CheckSquare size={15} /></Btn>
          <Btn active={editor.isActive('blockquote')} onClick={() => chain().toggleBlockquote().run()} title="Block quote"><Quote size={15} /></Btn>
          <Btn active={editor.isActive('link')} onClick={setLink} title="Link"><LinkIcon size={15} /></Btn>
          <Btn onClick={() => fileRef.current?.click()} title="Insert image"><ImageIcon size={15} /></Btn>
          <Btn onClick={() => chain().setHorizontalRule().run()} title="Horizontal rule"><Minus size={15} /></Btn>
          <Btn onClick={() => chain().insertSectionDivider().run()} title="Section divider"><SeparatorHorizontal size={15} /></Btn>
          <Btn onClick={() => chain().insertPageBreak().run()} title="Page break">⇩</Btn>
        </div>

        {/* --- tables --- */}
        <div className="lr-rt-group">
          <Menu title="Table" icon={<TableIcon size={15} />} label="Table" width={260}>
            <MenuItem onClick={() => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>
              Insert 3 × 3 with header row
            </MenuItem>
            <MenuItem onClick={() => chain().insertTable({ rows: 5, cols: 4, withHeaderRow: true }).run()}>
              Insert 5 × 4 with header row
            </MenuItem>
            <MenuItem onClick={() => chain().insertTable({ rows: 2, cols: 2, withHeaderRow: false }).run()}>
              Insert 2 × 2, no header
            </MenuItem>
            <div className="lr-rt-menu-sep" />
            {TABLE_STYLES.map((style) => (
              <MenuItem key={style.value} onClick={() => setTableClass(style.value)}>{style.label}</MenuItem>
            ))}
          </Menu>

          <Menu title="Rows" icon={<Rows3 size={15} />} width={220}>
            <MenuItem onClick={() => chain().addRowBefore().run()}>Insert row above</MenuItem>
            <MenuItem onClick={() => chain().addRowAfter().run()}>Insert row below</MenuItem>
            <MenuItem onClick={() => chain().deleteRow().run()}>Delete row</MenuItem>
            <MenuItem onClick={() => chain().toggleHeaderRow().run()}>Toggle header row</MenuItem>
            <div className="lr-rt-menu-sep" />
            {ROW_HEIGHTS.map((h) => (
              <MenuItem key={h.label}
                onClick={() => chain().setCellAttribute('rowHeight', h.value).run()}>
                Row height: {h.label}
              </MenuItem>
            ))}
          </Menu>

          <Menu title="Columns" icon={<Columns3 size={15} />} width={220}>
            <MenuItem onClick={() => chain().addColumnBefore().run()}>Insert column left</MenuItem>
            <MenuItem onClick={() => chain().addColumnAfter().run()}>Insert column right</MenuItem>
            <MenuItem onClick={() => chain().deleteColumn().run()}>Delete column</MenuItem>
            <MenuItem onClick={() => chain().toggleHeaderColumn().run()}>Toggle header column</MenuItem>
            <div className="lr-rt-menu-sep" />
            <MenuItem onClick={() => chain().fixTables().run()}>Auto-fit / repair table</MenuItem>
            <MenuItem onClick={() => {
              // Equal column width = clear every explicit colwidth and let the
              // table lay itself out again.
              chain().setCellAttribute('colwidth', null).run();
            }}>Equal column widths</MenuItem>
          </Menu>

          <Menu title="Cells" icon={<Scissors size={15} />} width={200}>
            <MenuItem onClick={() => chain().mergeCells().run()}>Merge cells</MenuItem>
            <MenuItem onClick={() => chain().splitCell().run()}>Split cell</MenuItem>
            <MenuItem onClick={() => chain().toggleHeaderCell().run()}>Toggle header cell</MenuItem>
            <div className="lr-rt-menu-sep" />
            <MenuItem onClick={() => chain().setCellAttribute('align', 'left').run()}>Align cell left</MenuItem>
            <MenuItem onClick={() => chain().setCellAttribute('align', 'center').run()}>Align cell centre</MenuItem>
            <MenuItem onClick={() => chain().setCellAttribute('align', 'right').run()}>Align cell right</MenuItem>
          </Menu>

          <Btn disabled={!inTable} onClick={() => chain().deleteTable().run()} title="Delete table"><Trash2 size={15} /></Btn>
        </div>
      </div>

      <input ref={fileRef} type="file" hidden accept="image/png,image/jpeg,image/gif,image/webp"
        onChange={(e) => { addImage(e.target.files?.[0]); e.target.value = ''; }} />
      {imageError && <p role="alert" className="lr-rt-error">{imageError}</p>}

      <EditorContent editor={editor} className="lr-rt-content" style={{ minHeight }} />

      <p className="lr-rt-hint">
        Drag a column border to resize it. Tables, images and formatting are
        preserved in the PDF.
      </p>
    </div>
  );
};

export default TiptapEditor;
