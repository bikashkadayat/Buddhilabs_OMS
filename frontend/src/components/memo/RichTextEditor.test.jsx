import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import RichTextEditor from './RichTextEditor';

/**
 * The read path only. The editable surface is a lazy-loaded ProseMirror instance
 * that jsdom cannot lay out meaningfully; its behaviour is covered by the
 * sanitizer tests on the backend (which is the authority) plus these, which prove
 * the read-path allowlist did not lag behind it.
 *
 * That lag is the failure worth guarding: content saves fine, the API returns it
 * intact, and the browser then strips it on render — so the author only discovers
 * their table is gone after a reload.
 */
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==';

const renderRO = (value) => render(<RichTextEditor value={value} readOnly />);

describe('RichTextEditor (read-only)', () => {
  it('renders an empty-content notice for a blank body', () => {
    renderRO('');
    expect(screen.getByText('No content.')).toBeInTheDocument();
  });

  it('carries the memo-body class, so screen matches the PDF stylesheet', () => {
    const { container } = renderRO('<p>x</p>');
    expect(container.querySelector('.memo-body')).not.toBeNull();
  });

  it('keeps a table with its header, spans and column widths', () => {
    const { container } = renderRO(
      '<table class="memo-table memo-table-financial">'
      + '<thead><tr><th colspan="2">Head</th></tr></thead>'
      + '<tbody><tr><td colwidth="180" style="text-align:right">1</td>'
      + '<td rowspan="2">2</td></tr></tbody></table>',
    );
    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table.className).toContain('memo-table-financial');
    expect(container.querySelector('th').getAttribute('colspan')).toBe('2');
    expect(container.querySelector('td').getAttribute('colwidth')).toBe('180');
    expect(container.querySelector('td').style.textAlign).toBe('right');
  });

  it('keeps inline colour, highlight, font and spacing', () => {
    const { container } = renderRO(
      '<p style="text-align:center;line-height:2">'
      + '<span style="color:#b91c1c;background-color:#fef08a;font-size:18px;'
      + 'font-family:Georgia">x</span></p>',
    );
    const p = container.querySelector('p');
    const span = container.querySelector('span');
    expect(p.style.textAlign).toBe('center');
    expect(p.style.lineHeight).toBe('2');
    expect(span.style.color).toBeTruthy();
    expect(span.style.fontSize).toBe('18px');
  });

  it('keeps an embedded raster image', () => {
    const { container } = renderRO(`<p><img src="${PNG}" alt="chart"></p>`);
    expect(container.querySelector('img').getAttribute('src')).toBe(PNG);
  });

  it('keeps checklists, page breaks and dividers', () => {
    const { container } = renderRO(
      '<ul data-type="taskList"><li data-checked="true">done</li></ul>'
      + '<div class="memo-page-break"></div><div class="memo-divider"></div>',
    );
    expect(container.querySelector('ul[data-type="taskList"]')).not.toBeNull();
    expect(container.querySelector('li').getAttribute('data-checked')).toBe('true');
    expect(container.querySelector('.memo-page-break')).not.toBeNull();
    expect(container.querySelector('.memo-divider')).not.toBeNull();
  });

  it('still strips scripts, handlers and non-raster image sources', () => {
    const { container } = renderRO(
      '<p>ok</p><script>window.hacked = 1</script>'
      + `<img src="${PNG}" onerror="window.hacked = 1">`
      + '<img src="data:image/svg+xml;base64,PHN2Zz4=">'
      + '<iframe src="https://evil.test"></iframe>',
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
    expect(container.querySelector('img[onerror]')).toBeNull();
    // The raster image survives; the SVG one is removed entirely - not merely
    // stripped of its src, which would leave a broken-image icon.
    const sources = [...container.querySelectorAll('img')].map((i) => i.getAttribute('src'));
    expect(sources).toEqual([PNG]);
    expect(window.hacked).toBeUndefined();
  });

  it('strips a javascript: link but keeps the text', () => {
    const { container } = renderRO('<p><a href="javascript:alert(1)">click</a></p>');
    expect(container.querySelector('a')?.getAttribute('href')).toBeNull();
    expect(screen.getByText('click')).toBeInTheDocument();
  });
});
