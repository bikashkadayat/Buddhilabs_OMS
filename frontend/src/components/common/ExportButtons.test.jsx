/**
 * Phase FIX — the shared export control.
 *
 * Five pages had their own copy of the broken `<a href>` pattern. This is the
 * one place the rule now lives, so it is the one place worth testing directly.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/reportService', () => ({ saveBlob: vi.fn() }));

import ExportButtons from './ExportButtons';
import { saveBlob } from '../../services/reportService';

const response = { data: new Blob(['a,b']), headers: {} };

beforeEach(() => vi.clearAllMocks());

describe('exporting', () => {
  it('fetches through the caller\'s authenticated client and saves the blob',
    async () => {
      const user = userEvent.setup();
      const download = vi.fn().mockResolvedValue(response);
      render(<ExportButtons download={download} name="thing" />);

      await user.click(screen.getByRole('button', { name: /CSV/ }));
      expect(download).toHaveBeenCalledWith('csv');
      expect(saveBlob).toHaveBeenCalledWith(response, 'thing.csv');
    });

  it('renders buttons, never links — a link sends no auth header', () => {
    render(<ExportButtons download={vi.fn()} name="thing" />);
    expect(screen.queryAllByRole('link')).toHaveLength(0);
    expect(screen.getAllByRole('button')).toHaveLength(2);
  });

  it('offers only the formats it was given', () => {
    render(<ExportButtons download={vi.fn()} name="t" formats={['csv']} />);
    expect(screen.queryByRole('button', { name: /PDF/ })).toBeNull();
  });

  it('names the file with the right extension per format', async () => {
    const user = userEvent.setup();
    const download = vi.fn().mockResolvedValue(response);
    render(<ExportButtons download={download} name="t"
      formats={['csv', 'pdf', 'excel']} />);

    await user.click(screen.getByRole('button', { name: /PDF/ }));
    expect(saveBlob).toHaveBeenCalledWith(response, 't.pdf');
    await user.click(screen.getByRole('button', { name: /Excel/ }));
    expect(saveBlob).toHaveBeenCalledWith(response, 't.xlsx');
  });

  it('disables every button while one export is in flight', async () => {
    const user = userEvent.setup();
    let release;
    const download = vi.fn(() => new Promise((r) => { release = r; }));
    render(<ExportButtons download={download} name="t" />);

    await user.click(screen.getByRole('button', { name: /CSV/ }));
    expect(screen.getByRole('button', { name: /PDF/ })).toBeDisabled();
    release(response);
  });
});

describe('when an export is refused', () => {
  it('reads the message out of a BLOB error body', async () => {
    // The subtle part. With responseType: 'blob' the ERROR body is a Blob too,
    // so reading `err.response.data.detail` gives undefined and the user is
    // told nothing. It has to be read back as text first.
    const user = userEvent.setup();
    const download = vi.fn().mockRejectedValue({
      response: {
        data: new Blob([JSON.stringify({ detail: 'Not your report.' })]),
      },
    });
    render(<ExportButtons download={download} name="t" />);

    await user.click(screen.getByRole('button', { name: /CSV/ }));
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Not your report.');
  });

  it('falls back to a plain sentence when the body is not JSON', async () => {
    const user = userEvent.setup();
    const download = vi.fn().mockRejectedValue({
      response: { data: new Blob(['<html>500</html>']) },
    });
    render(<ExportButtons download={download} name="t" />);

    await user.click(screen.getByRole('button', { name: /CSV/ }));
    expect(await screen.findByRole('alert'))
      .toHaveTextContent(/could not be downloaded/i);
  });

  it('re-enables the buttons so the export can be retried', async () => {
    const user = userEvent.setup();
    const download = vi.fn().mockRejectedValue(new Error('offline'));
    render(<ExportButtons download={download} name="t" />);

    await user.click(screen.getByRole('button', { name: /CSV/ }));
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: /CSV/ })).toBeEnabled();
  });
});
