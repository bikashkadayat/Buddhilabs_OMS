import React from 'react';
import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import TiptapEditor from './TiptapEditor';

/**
 * Mounts the real editor with its real extension list.
 *
 * jsdom cannot lay out ProseMirror, so this is not a test of drag-resizing a
 * column. What it does prove is that the extension set CONSTRUCTS: a TipTap
 * extension with a bad name, a duplicated mark, a malformed
 * addGlobalAttributes or a schema conflict throws at editor creation, and four of
 * the extensions here are hand-written. That failure would otherwise only appear
 * the first time a user clicked "edit".
 *
 * It also asserts the toolbar surface, since a control that never renders cannot
 * be used no matter how well the command behind it works.
 */
const renderEditor = (props = {}) =>
  render(<TiptapEditor value="<p>Hello</p>" onChange={() => {}} {...props} />);

describe('TiptapEditor', () => {
  it('constructs with the full extension set and renders its content', async () => {
    renderEditor();
    // The toolbar only exists once useEditor has returned an editor; before that
    // the component renders a plain textarea fallback.
    await waitFor(() => expect(screen.getByRole('toolbar')).toBeInTheDocument());
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });

  it('offers every text-formatting control', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    ['Bold', 'Italic', 'Underline', 'Strikethrough', 'Subscript', 'Superscript']
      .forEach((label) => expect(screen.getByLabelText(label)).toBeInTheDocument());
  });

  it('offers alignment, spacing and indentation', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    ['Align left', 'Align centre', 'Align right', 'Justify',
      'Increase indent', 'Decrease indent', 'Line spacing']
      .forEach((label) => expect(screen.getByLabelText(label)).toBeInTheDocument());
  });

  it('offers the typeface menus that have no official TipTap v2 extension', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    // Font size and line spacing are hand-written; their absence would mean the
    // custom extension failed to register its commands.
    expect(screen.getByLabelText('Font size')).toBeInTheDocument();
    expect(screen.getByLabelText('Font family')).toBeInTheDocument();
    expect(screen.getByLabelText('Text colour')).toBeInTheDocument();
    expect(screen.getByLabelText('Highlight')).toBeInTheDocument();
  });

  it('offers the content blocks', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    ['Bullet list', 'Numbered list', 'Checklist', 'Block quote', 'Link',
      'Insert image', 'Horizontal rule', 'Section divider', 'Page break']
      .forEach((label) => expect(screen.getByLabelText(label)).toBeInTheDocument());
  });

  it('offers the table, row, column and cell menus', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    ['Table', 'Rows', 'Columns', 'Cells', 'Delete table']
      .forEach((label) => expect(screen.getByLabelText(label)).toBeInTheDocument());
  });

  it('exposes every table operation the spec asks for', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));

    // fireEvent, not node.click(): the menus open from a React onClick handler,
    // and a bare DOM click does not drive React's synthetic event system here.
    const open = (menu) => fireEvent.click(screen.getByLabelText(menu));
    open('Rows');
    ['Insert row above', 'Insert row below', 'Delete row', 'Toggle header row']
      .forEach((t) => expect(screen.getByText(t)).toBeInTheDocument());

    open('Columns');
    ['Insert column left', 'Insert column right', 'Delete column',
      'Equal column widths', 'Auto-fit / repair table']
      .forEach((t) => expect(screen.getByText(t)).toBeInTheDocument());

    open('Cells');
    ['Merge cells', 'Split cell', 'Toggle header cell']
      .forEach((t) => expect(screen.getByText(t)).toBeInTheDocument());
  });

  it('offers table presets that match the classes the sanitizer allows', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));
    fireEvent.click(screen.getByLabelText('Table'));
    // A preset whose class the backend strips would silently lose its styling on
    // save, so the label set here is the contract with ALLOWED_CLASSES.
    ['Standard', 'Bordered', 'Striped rows', 'Financial (right-aligned)', 'Compact']
      .forEach((t) => expect(screen.getByText(t)).toBeInTheDocument());
    expect(screen.getByText('Insert 3 × 3 with header row')).toBeInTheDocument();
  });

  it('reports why an unsupported image was refused rather than failing silently', async () => {
    renderEditor();
    await waitFor(() => screen.getByRole('toolbar'));

    const input = document.querySelector('input[type="file"]');
    const bad = new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' });
    Object.defineProperty(input, 'files', { value: [bad], configurable: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));

    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Images must be PNG, JPEG, GIF or WebP.');
  });

});
