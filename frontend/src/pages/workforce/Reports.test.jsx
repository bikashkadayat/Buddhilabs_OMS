import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../services/reportService', async () => {
  const actual = await vi.importActual('../../services/reportService');
  return {
    ...actual,
    reportService: {
      requestReport: vi.fn(),
      getStatus: vi.fn(),
      download: vi.fn(),
    },
    saveBlob: vi.fn(),
  };
});

import { reportService, saveBlob, WORKFORCE_REPORTS } from '../../services/reportService';
import Reports from './Reports';

const renderPage = () => render(<MemoryRouter><Reports /></MemoryRouter>);

describe('Workforce report center', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reportService.requestReport.mockResolvedValue({ id: 'r1', status: 'ready' });
    reportService.getStatus.mockResolvedValue({ status: 'ready' });
    reportService.download.mockResolvedValue({ data: new Blob(['x']), headers: {} });
  });

  it('lists every workforce report', () => {
    renderPage();
    expect(WORKFORCE_REPORTS.length).toBe(8);
    WORKFORCE_REPORTS.forEach((report) => {
      expect(screen.getByText(report.name)).toBeInTheDocument();
    });
  });

  it('offers PDF, Excel and CSV on every report', () => {
    renderPage();
    expect(screen.getAllByRole('button', { name: /^PDF$/ })).toHaveLength(8);
    expect(screen.getAllByRole('button', { name: /^Excel$/ })).toHaveLength(8);
    expect(screen.getAllByRole('button', { name: /^CSV$/ })).toHaveLength(8);
  });

  it('requests the right report type and format', async () => {
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^CSV$/ })[0]);
    await waitFor(() => expect(reportService.requestReport).toHaveBeenCalledWith(
      WORKFORCE_REPORTS[0].key,
      expect.objectContaining({ format: 'csv' }),
    ));
  });

  it('passes the selected window through', async () => {
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^Excel$/ })[2]);
    await waitFor(() => {
      const params = reportService.requestReport.mock.calls[0][1];
      expect(params.from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(params.to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
  });

  it('downloads the file once the run is ready', async () => {
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^PDF$/ })[0]);
    await waitFor(() => expect(reportService.download).toHaveBeenCalledWith('r1'));
    await waitFor(() => expect(saveBlob).toHaveBeenCalled());
  });

  it('names the downloaded file with the right extension', async () => {
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^Excel$/ })[0]);
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(
      expect.anything(),
      `${WORKFORCE_REPORTS[0].key}.xlsx`,
    ));
  });

  it('surfaces a failed run instead of downloading nothing', async () => {
    reportService.requestReport.mockResolvedValue({ id: 'r2', status: 'failed' });
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^CSV$/ })[0]);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(reportService.download).not.toHaveBeenCalled();
  });

  it('surfaces a request error', async () => {
    reportService.requestReport.mockRejectedValue({
      response: { data: { detail: 'You are not permitted to run this report type.' } },
    });
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /^CSV$/ })[0]);
    await waitFor(() => expect(
      screen.getByText('You are not permitted to run this report type.'),
    ).toBeInTheDocument());
  });
});
