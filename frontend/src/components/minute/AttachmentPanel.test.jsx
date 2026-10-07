import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import AttachmentPanel from './AttachmentPanel';

/**
 * The behaviour worth pinning here is what the panel REFUSES and what it does not
 * promise: a disallowed type never leaves the browser, an oversized file never leaves
 * the browser, and "Preview" is only offered where the server says a browser can
 * actually render the file.
 */
const file = (over = {}) => ({
  id: 'f1',
  original_name: 'board-pack.pdf',
  extension: 'pdf',
  size: 204800,
  size_label: '200 KB',
  is_previewable: true,
  version: 1,
  is_current: true,
  download_url: '/api/v1/media/?p=x&e=1&s=abc&dl=1',
  preview_url: '/api/v1/media/?p=x&e=1&s=abc',
  versions: [],
  uploaded_by: { id: 'u1', full_name: 'Asha Rai' },
  uploaded_at: '2026-08-12T09:00:00Z',
  ...over,
});

const pick = (name, bytes = 1024) => {
  const blob = new File(['x'.repeat(bytes)], name);
  Object.defineProperty(blob, 'size', { value: bytes });
  return blob;
};

const choose = (label, files) => {
  const input = screen.getByLabelText(label);
  Object.defineProperty(input, 'files', { value: files, configurable: true });
  fireEvent.change(input);
};

describe('AttachmentPanel', () => {
  it('lists a file with its type, size, uploader and date', () => {
    render(<AttachmentPanel attachments={[file()]} />);
    expect(screen.getByText('board-pack.pdf')).toBeInTheDocument();
    expect(screen.getByText(/PDF/)).toBeInTheDocument();
    expect(screen.getByText(/200 KB/)).toBeInTheDocument();
    expect(screen.getByText(/Asha Rai/)).toBeInTheDocument();
  });

  it('says so plainly when nothing is attached', () => {
    render(<AttachmentPanel attachments={[]} />);
    expect(screen.getByText(/No documents are attached/)).toBeInTheDocument();
  });

  /**
   * The URLs must come from the server, signed and expiring. Phase 31 built them from
   * MEDIA_URL, a route this project does not serve, so every link was broken.
   */
  it('links to the server-signed URLs and never builds a media path', () => {
    render(<AttachmentPanel attachments={[file()]} />);
    const download = screen.getByLabelText('Download board-pack.pdf');
    expect(download).toHaveAttribute('href', expect.stringContaining('/api/v1/media/?'));
    expect(download.getAttribute('href')).toContain('dl=1');
    expect(download.getAttribute('href')).not.toMatch(/^\/media\//);
  });

  it('offers Preview only where the server says it can be rendered', () => {
    const { rerender } = render(<AttachmentPanel attachments={[file()]} />);
    expect(screen.getByLabelText('Preview board-pack.pdf')).toBeInTheDocument();

    rerender(<AttachmentPanel attachments={[file({
      original_name: 'budget.docx', extension: 'docx',
      is_previewable: false, preview_url: null,
    })]} />);
    expect(screen.queryByLabelText('Preview budget.docx')).toBeNull();
    // Still downloadable — the honest offer for an Office document.
    expect(screen.getByLabelText('Download budget.docx')).toBeInTheDocument();
  });

  it('hides every editing control when the minute is read-only', () => {
    render(<AttachmentPanel attachments={[file()]} canEdit={false} />);
    expect(screen.queryByRole('button', { name: /Attach files/ })).toBeNull();
    expect(screen.queryByLabelText('Remove board-pack.pdf')).toBeNull();
    expect(screen.queryByRole('button', { name: /New version/ })).toBeNull();
    // But it is still downloadable: archived means read-only, not gone.
    expect(screen.getByLabelText('Download board-pack.pdf')).toBeInTheDocument();
  });

  it('uploads the chosen files', () => {
    const onUpload = vi.fn();
    render(<AttachmentPanel attachments={[]} canEdit onUpload={onUpload} />);
    choose('Attach files', [pick('a.pdf'), pick('b.png')]);
    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(onUpload.mock.calls[0][0]).toHaveLength(2);
  });

  it('refuses a disallowed type before it leaves the browser', () => {
    const onUpload = vi.fn();
    render(<AttachmentPanel attachments={[]} canEdit onUpload={onUpload} />);
    choose('Attach files', [pick('payload.sh')]);
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/not an allowed file type/);
  });

  it('refuses an oversized file before it leaves the browser', () => {
    const onUpload = vi.fn();
    render(<AttachmentPanel attachments={[]} canEdit onUpload={onUpload} />);
    choose('Attach files', [pick('huge.pdf', 26 * 1024 * 1024)]);
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/larger than 25MB/);
  });

  it('names the offending file when one of several is wrong', () => {
    const onUpload = vi.fn();
    render(<AttachmentPanel attachments={[]} canEdit onUpload={onUpload} />);
    choose('Attach files', [pick('fine.pdf'), pick('nope.exe')]);
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('nope.exe');
  });

  it('shows the version badge and reveals earlier versions on request', () => {
    render(<AttachmentPanel attachments={[file({
      version: 3,
      versions: [
        { id: 'v2', version: 2, size_label: '190 KB', uploaded_by: 'Asha Rai',
          uploaded_at: '2026-08-11T09:00:00Z', download_url: '/api/v1/media/?p=v2' },
        { id: 'v1', version: 1, size_label: '180 KB', uploaded_by: 'Asha Rai',
          uploaded_at: '2026-08-10T09:00:00Z', download_url: '/api/v1/media/?p=v1' },
      ],
    })]} />);

    expect(screen.getByText('v3')).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: /2 earlier/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    // Each old version is individually downloadable — that is the point of keeping them.
    expect(screen.getByLabelText('Download version 2 of board-pack.pdf'))
      .toHaveAttribute('href', '/api/v1/media/?p=v2');
    expect(screen.getByLabelText('Download version 1 of board-pack.pdf'))
      .toBeInTheDocument();
  });

  it('uploads a replacement as a new version of the file it replaces', () => {
    const onUpload = vi.fn();
    render(<AttachmentPanel attachments={[file()]} canEdit onUpload={onUpload} />);
    fireEvent.click(screen.getByRole('button', { name: /New version/ }));
    choose('Upload a new version', [pick('board-pack.pdf')]);
    expect(onUpload).toHaveBeenCalledWith(expect.anything(), { replaces: 'f1' });
  });

  it('removes a file through the callback', () => {
    const onDelete = vi.fn();
    render(<AttachmentPanel attachments={[file()]} canEdit onDelete={onDelete} />);
    fireEvent.click(screen.getByLabelText('Remove board-pack.pdf'));
    expect(onDelete).toHaveBeenCalledWith('f1');
  });

  it('surfaces a server-side error', () => {
    render(<AttachmentPanel attachments={[]} canEdit
      error="board-pack.pdf: File content does not match the '.pdf' extension." />);
    expect(screen.getByRole('alert')).toHaveTextContent(/does not match/);
  });
});
