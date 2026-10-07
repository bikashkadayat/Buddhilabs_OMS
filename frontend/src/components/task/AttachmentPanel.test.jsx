/**
 * Phase T2.4 / T2.5 — attachments, evidence, links and the download log.
 *
 * The rules worth pinning in the UI: an upload copies the FileList before it
 * clears the input, an external link cannot leak the task URL in a Referer
 * header, and the download log is not offered to somebody the server would
 * refuse.
 */
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: { getDownloadLog: vi.fn() },
}));

import AttachmentPanel from './AttachmentPanel';
import { taskService } from '../../services/taskService';

const ME = 'u1';

const file = (extra = {}) => ({
  id: 'f1',
  kind: 'file',
  original_name: 'return.pdf',
  link_url: '',
  size: 2048,
  caption: '',
  is_evidence: true,
  is_removed: false,
  download_count: 3,
  download_url: '/api/v1/tasks/t1/attachments/f1/download/',
  uploaded_by: ME,
  uploaded_by_name: 'Me',
  uploaded_at: '2026-09-01T09:00:00Z',
  ...extra,
});

const link = (extra = {}) => file({
  id: 'l1', kind: 'link', original_name: 'Published report',
  link_url: 'https://example.org/report', size: 0, download_url: null, ...extra,
});

const renderPanel = (props = {}) => {
  const handlers = {
    onUpload: vi.fn(), onAddLink: vi.fn(), onFlag: vi.fn(), onRemove: vi.fn(),
  };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AttachmentPanel taskId="t1" files={[]} removed={[]} variant="evidence"
        canUpload canViewLog={false} currentUserId={ME} isOwner={false}
        busy={false} {...handlers} {...props} />
    </QueryClientProvider>,
  );
  return handlers;
};

describe('attachment panel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says so when there is no evidence', () => {
    renderPanel();
    expect(screen.getByText('No evidence uploaded yet.')).toBeInTheDocument();
  });

  it('uses the right wording for reference files', () => {
    renderPanel({ variant: 'reference' });
    expect(screen.getByText('No reference files.')).toBeInTheDocument();
  });

  it('FETCHES a file rather than linking to it', () => {
    /*
     * This test used to assert the opposite — that the filename was an <a> with
     * href="/api/v1/tasks/t1/attachments/f1/download/" — and it passed while
     * the feature was broken in production.
     *
     * That href is an AUTHENTICATED, task-scoped view. A browser navigation
     * carries no Authorization header, so clicking it downloaded a 401 body:
     * "Authentication credentials were not provided." The assertion was true
     * and useless, which is the same failure the downloads guard was written
     * for after nine export buttons did exactly this.
     *
     * So it now pins the behaviour instead of the markup: no link, a control
     * that calls the authenticated client.
     */
    renderPanel({ files: [file()] });
    expect(screen.queryByRole('link', { name: /return\.pdf/ })).toBeNull();
    expect(screen.getByRole('button', { name: /return\.pdf/ })).toBeInTheDocument();
  });

  it('opens an external link without leaking the task URL', () => {
    /* noreferrer as well as noopener: the Referer header would otherwise carry
       the internal task page an evidence link was reached from. */
    renderPanel({ files: [link()] });
    const anchor = screen.getByRole('link', { name: /Published report/ });
    expect(anchor).toHaveAttribute('target', '_blank');
    expect(anchor.getAttribute('rel')).toContain('noreferrer');
    expect(anchor.getAttribute('rel')).toContain('noopener');
  });

  it('copies the chosen files before clearing the input', () => {
    /* Clearing `value` empties `files`, so a later read would upload nothing. */
    const { onUpload } = renderPanel();
    const input = screen.getByLabelText(/upload evidence/i);
    const chosen = new File(['x'], 'evidence.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [chosen] } });

    expect(onUpload).toHaveBeenCalledTimes(1);
    const [files, isEvidence] = onUpload.mock.calls[0];
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe('evidence.png');
    expect(isEvidence).toBe(true);
  });

  it('adds a link with its caption', () => {
    const { onAddLink } = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /add a link/i }));
    fireEvent.change(screen.getByLabelText('Link URL'),
      { target: { value: 'https://example.org/report' } });
    fireEvent.change(screen.getByLabelText('Link caption'),
      { target: { value: 'Published report' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    expect(onAddLink).toHaveBeenCalledWith('https://example.org/report',
      'Published report');
  });

  it('offers no link button on the reference section', () => {
    renderPanel({ variant: 'reference' });
    expect(screen.queryByRole('button', { name: /add a link/i })).toBeNull();
  });

  it('lets the uploader reclassify and remove their own file', () => {
    const { onFlag, onRemove } = renderPanel({ files: [file()] });
    fireEvent.click(screen.getByRole('button', { name: /mark as reference/i }));
    expect(onFlag).toHaveBeenCalledWith('f1', false);
    fireEvent.click(screen.getByRole('button', { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledWith('f1');
  });

  it('offers neither to somebody who uploaded neither and owns nothing', () => {
    renderPanel({ files: [file({ uploaded_by: 'someone-else' })] });
    expect(screen.queryByRole('button', { name: /mark as reference/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull();
  });

  it('lets the task owner manage anything on it', () => {
    const { onRemove } = renderPanel({
      isOwner: true, files: [file({ uploaded_by: 'someone-else' })],
    });
    fireEvent.click(screen.getByRole('button', { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledWith('f1');
  });

  it('offers the download log only when the server grants it', () => {
    renderPanel({ files: [file()] });
    expect(screen.queryByRole('button', { name: /who opened it/i })).toBeNull();

    renderPanel({ files: [file()], canViewLog: true });
    expect(screen.getByRole('button', { name: /who opened it/i }))
      .toBeInTheDocument();
  });

  it('fetches the download log only when it is asked for', async () => {
    taskService.getDownloadLog.mockResolvedValue([
      { id: 'd1', user_name: 'Them', downloaded_at: '2026-09-02T09:00:00Z' },
    ]);
    renderPanel({ files: [file()], canViewLog: true });
    expect(taskService.getDownloadLog).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /who opened it/i }));
    expect(await screen.findByText('Them')).toBeInTheDocument();
    expect(taskService.getDownloadLog).toHaveBeenCalledWith('t1', 'f1');
  });

  it('never offers a download log for a link, which has no downloads', () => {
    renderPanel({ files: [link()], canViewLog: true });
    expect(screen.queryByRole('button', { name: /who opened it/i })).toBeNull();
  });

  it('keeps withdrawn files behind a disclosure', () => {
    renderPanel({
      files: [file()],
      removed: [file({ id: 'f0', original_name: 'old.pdf', is_removed: true,
        removed_by_name: 'Me', removed_at: '2026-09-02T09:00:00Z' })],
    });
    expect(screen.queryByText('old.pdf')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /withdrawal history/i }));
    expect(screen.getByText('old.pdf')).toBeInTheDocument();
    expect(screen.getByText(/removed by Me/)).toBeInTheDocument();
  });

  it('shows the download count on a file that has been opened', () => {
    renderPanel({ files: [file()] });
    // The filename is a button now, not a link — see the fetch test above.
    const row = screen.getByRole('button', { name: /return\.pdf/ }).closest('li');
    expect(within(row).getByText(/3 downloads/)).toBeInTheDocument();
  });
});
