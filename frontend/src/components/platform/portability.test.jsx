import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * Phase S6.5: the console's side of export, archive and restore.
 *
 * The assertions that matter most are about what the operator is TOLD.
 * "Archive" sounds like "delete" to most people and means nearly the opposite
 * here, and a failed export arrives as a successful HTTP response carrying a
 * receipt that says it failed — both are places where a plausible-looking UI
 * would mislead somebody into an irreversible-feeling decision.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import PortabilityPanel from './PortabilityPanel';

const ORG = { name: 'ABC School', slug: 'abc-school', status: 'trial' };

const READY = {
  id: 'e1', organization_slug: 'abc-school', status: 'ready',
  status_display: 'Ready', contents: 'full', requested_by_email: 'ops@p.test',
  row_count: 412, media_count: 9, size_bytes: 1048576,
  sha256: 'a'.repeat(64), error: '', created_at: '2026-10-05T10:00:00Z',
  completed_at: '2026-10-05T10:00:04Z', expires_at: '2026-11-04T10:00:00Z',
  downloaded_at: null, download_count: 0, is_downloadable: true, tables: {},
};

const health = (overrides = {}) => ({
  archive_state: {
    is_archived: false, can_archive: true, can_restore: false,
    archived_at: null, archived_reason: '', status_before_archive: null,
    ...overrides,
  },
});

const wrap = (props = {}) => render(
  <MemoryRouter>
    <PortabilityPanel slug="abc-school" organization={ORG} health={health()}
                      {...props} />
  </MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: [] });
});

describe('export', () => {
  it('reports what the bundle actually contains', async () => {
    api.post.mockResolvedValue({ data: READY });
    wrap();
    await waitFor(() => expect(screen.getByText('Export data')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Export data'));
    await waitFor(() => expect(
      screen.getByText(/412 rows, 9 file\(s\)/)).toBeInTheDocument());
    expect(api.post).toHaveBeenCalledWith(
      '/platform/organizations/abc-school/exports/', { contents: 'full' });
  });

  it('shows a refused export as a result with its reason, not as a crash',
    async () => {
      // The server answers 201 with `status: failed` when the integrity pass
      // refuses the bundle: the request was handled and that IS the answer.
      // Rendering it as an error would suggest the console broke.
      api.post.mockResolvedValue({
        data: { ...READY, status: 'failed', is_downloadable: false,
                error: 'integrity check failed: 3 dangling reference(s)' },
      });
      wrap();
      fireEvent.click(await screen.findByText('Export data'));
      await waitFor(() => expect(
        screen.getByText(/integrity check failed/)).toBeInTheDocument());
    });

  it('tells the operator the checksum to verify a handed-over file against',
    async () => {
      api.get.mockImplementation((url, config) => {
        if (config?.responseType === 'blob') {
          return Promise.resolve({
            data: new Blob(['zip']),
            headers: { 'x-export-sha256': 'b'.repeat(64) },
          });
        }
        return Promise.resolve({ data: [READY] });
      });
      // jsdom has neither of these.
      window.URL.createObjectURL = vi.fn(() => 'blob:x');
      window.URL.revokeObjectURL = vi.fn();

      wrap();
      fireEvent.click(await screen.findByText('Download'));
      await waitFor(() => expect(
        screen.getByText(/Verify the file against SHA-256 bbb/))
        .toBeInTheDocument());
    });

  it('offers no download for an export that has no file', async () => {
    api.get.mockResolvedValue({
      data: [{ ...READY, status: 'expired', is_downloadable: false }],
    });
    wrap();
    await waitFor(() => expect(screen.getByText('discarded')).toBeInTheDocument());
    expect(screen.queryByText('Download')).not.toBeInTheDocument();
  });
});

describe('archive', () => {
  it('says both halves before locking a customer out', async () => {
    // "Archive" reads as "delete" to most people and means nearly the
    // opposite here. Somebody who wanted the data gone must not be able to
    // believe they achieved it by pressing this.
    wrap();
    fireEvent.click(await screen.findByText('Archive workspace'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('locked out immediately');
    expect(dialog).toHaveTextContent('Nothing is deleted');
  });

  it('will not archive without a typed reason', async () => {
    wrap();
    fireEvent.click(await screen.findByText('Archive workspace'));
    const confirm = (await screen.findAllByText('Archive workspace'))
      .find((node) => node.closest('[role="dialog"]'));
    expect(confirm).toBeDisabled();
  });

  it('archives with the reason and reports that nothing was deleted', async () => {
    api.post.mockResolvedValue({ data: {} });
    const onChanged = vi.fn();
    wrap({ onChanged });

    fireEvent.click(await screen.findByText('Archive workspace'));
    fireEvent.change(screen.getByPlaceholderText(/Customer closed/),
      { target: { value: 'Customer closed their account' } });
    const confirm = screen.getAllByText('Archive workspace')
      .find((node) => node.closest('[role="dialog"]'));
    fireEvent.click(confirm);

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/platform/organizations/abc-school/archive/',
      { reason: 'Customer closed their account' }));
    await waitFor(() => expect(
      screen.getByText(/Nothing was deleted/)).toBeInTheDocument());
    expect(onChanged).toHaveBeenCalled();
  });

  it('surfaces the refusal when the tenant cannot be archived', async () => {
    api.post.mockRejectedValue({
      response: { data: { detail: 'A tenant that is still provisioning cannot be archived' } },
    });
    wrap();
    fireEvent.click(await screen.findByText('Archive workspace'));
    fireEvent.change(screen.getByPlaceholderText(/Customer closed/),
      { target: { value: 'Never used' } });
    fireEvent.click(screen.getAllByText('Archive workspace')
      .find((node) => node.closest('[role="dialog"]')));
    await waitFor(() => expect(
      screen.getByText(/still provisioning/)).toBeInTheDocument());
  });
});

describe('an archived workspace', () => {
  const archivedProps = {
    organization: { ...ORG, status: 'archived' },
    health: health({
      is_archived: true, can_archive: false, can_restore: true,
      archived_at: '2026-10-01T09:00:00Z',
      archived_reason: 'Customer closed their account',
      status_before_archive: 'active',
    }),
  };

  it('states that nothing was deleted and where a restore lands', async () => {
    wrap(archivedProps);
    const notice = await screen.findByText(/This workspace is archived/);
    const box = notice.closest('.pf-alert');
    expect(box).toHaveTextContent('Nothing has been deleted');
    expect(box).toHaveTextContent('active');
    expect(box).toHaveTextContent('Customer closed their account');
  });

  it('offers a restore instead of an archive', async () => {
    wrap(archivedProps);
    expect(await screen.findByText('Restore workspace')).toBeInTheDocument();
    expect(screen.queryByText('Archive workspace')).not.toBeInTheDocument();
  });

  it('still offers an export, which is usually why it was archived', async () => {
    // Handing a departed customer their data generally happens weeks after
    // the workspace was closed.
    wrap(archivedProps);
    expect(await screen.findByText('Export data')).toBeInTheDocument();
  });

  it('restores on request', async () => {
    api.post.mockResolvedValue({ data: {} });
    const onChanged = vi.fn();
    wrap({ ...archivedProps, onChanged });

    fireEvent.click(await screen.findByText('Restore workspace'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/platform/organizations/abc-school/restore/', {}));
    await waitFor(() => expect(
      screen.getByText(/restored to the state it was archived in/))
      .toBeInTheDocument());
    expect(onChanged).toHaveBeenCalled();
  });
});
