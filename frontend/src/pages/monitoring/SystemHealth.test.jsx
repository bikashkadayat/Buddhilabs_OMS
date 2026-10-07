import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../hooks/useMonitoring', () => ({
  useSystemHealth: vi.fn(),
  useAlertStatus: vi.fn(() => ({ data: ALERTS })),
}));

import { useAlertStatus, useSystemHealth } from '../../hooks/useMonitoring';
import SystemHealth from './SystemHealth';
import { PERMISSIONS, can } from '../../services/roles';

const HEALTHY = {
  status: 'green',
  generated_at: '2026-08-05T10:14:03+05:45',
  summary: { red: 0, amber: 0, green: 2 },
  sections: [
    {
      key: 'database', label: 'Database', state: 'green',
      metrics: [
        { key: 'db_reachable', label: 'Database reachable', value: true, state: 'green', unit: null, detail: null, thresholds: null },
        { key: 'disk_free_pct', label: 'Disk free', value: 64.2, state: 'green', unit: '%', detail: '120 GB of 190 GB free', thresholds: { amber: 20, red: 10 } },
      ],
    },
    {
      key: 'backups', label: 'Backups', state: 'green',
      metrics: [
        { key: 'backup_backup', label: 'Database backup', value: 3.2, state: 'green', unit: 'h ago', detail: 'on_schedule', thresholds: { amber: 26, red: 48 } },
      ],
    },
  ],
};

const FAILING = {
  ...HEALTHY,
  status: 'red',
  summary: { red: 1, amber: 1, green: 0 },
  sections: [
    { ...HEALTHY.sections[0], state: 'amber' },
    {
      key: 'backups', label: 'Backups', state: 'red',
      metrics: [
        { key: 'backup_backup', label: 'Database backup', value: null, state: 'red', unit: 'h ago', detail: 'Never run — this is the failure H4 was about.', thresholds: { amber: 26, red: 48 } },
      ],
    },
  ],
};

const ALERTS = { firing: [], configured: true, recipients: 1, history: [] };

const renderPage = () => render(<MemoryRouter><SystemHealth /></MemoryRouter>);

describe('System health page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAlertStatus.mockReturnValue({ data: ALERTS });
  });

  it('shows a loading skeleton', () => {
    useSystemHealth.mockReturnValue({ isLoading: true });
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows the failure rather than retrying silently', () => {
    // On this page in particular, a failed health check IS the answer.
    useSystemHealth.mockReturnValue({
      isError: true, error: new Error('unreachable'), refetch: vi.fn() });
    renderPage();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('renders every section and metric', () => {
    useSystemHealth.mockReturnValue({ data: HEALTHY, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText('Database')).toBeInTheDocument();
    expect(screen.getByText('Backups')).toBeInTheDocument();
    expect(screen.getByText('Disk free')).toBeInTheDocument();
  });

  it('shows thresholds beside the value, not just the number', () => {
    useSystemHealth.mockReturnValue({ data: HEALTHY, refetch: vi.fn() });
    renderPage();
    // "12%" alone tells you nothing about whether to act.
    expect(screen.getByText(/amber 20 · red 10/)).toBeInTheDocument();
  });

  it('sorts failing sections to the top', () => {
    useSystemHealth.mockReturnValue({ data: FAILING, refetch: vi.fn() });
    const { container } = renderPage();
    const headings = [...container.querySelectorAll('.mon-section h2')]
      .map((node) => node.textContent);
    expect(headings[0]).toContain('Backups');
  });

  it('renders a never-run backup as an em dash, never as zero', () => {
    useSystemHealth.mockReturnValue({ data: FAILING, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText(/Never run/)).toBeInTheDocument();
  });

  it('warns when no alert recipients are configured', () => {
    // A green board with nobody to tell is worse than no board at all.
    useSystemHealth.mockReturnValue({ data: HEALTHY, refetch: vi.fn() });
    useAlertStatus.mockReturnValue({ data: { ...ALERTS, configured: false } });
    renderPage();
    expect(screen.getByText(/No alert recipients configured/)).toBeInTheDocument();
  });

  it('lists active alerts with their remediation', () => {
    useSystemHealth.mockReturnValue({ data: FAILING, refetch: vi.fn() });
    useAlertStatus.mockReturnValue({
      data: {
        ...ALERTS,
        firing: [{
          key: 'backup_backup', severity: 'critical',
          title: 'Database backup missing', value: null, unit: 'h ago',
          detail: 'Never run', section: 'Backups',
          action: 'No successful backup in over a day. Fix before anything else.',
        }],
      },
    });
    renderPage();
    expect(screen.getByText('Database backup missing')).toBeInTheDocument();
    expect(screen.getByText(/Fix before anything else/)).toBeInTheDocument();
    expect(screen.getByText('critical')).toBeInTheDocument();
  });

  it('says so plainly when nothing is wrong', () => {
    useSystemHealth.mockReturnValue({ data: HEALTHY, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText(/No active alerts/)).toBeInTheDocument();
  });
});

describe('Monitoring permissions', () => {
  it('is HR and Admin only', () => {
    expect(can('maker', 'systemMonitoring')).toBe(false);
    expect(can('checker', 'systemMonitoring')).toBe(false);
    expect(can('approver', 'systemMonitoring')).toBe(true);
    expect(can('admin', 'systemMonitoring')).toBe(true);
  });

  it('mirrors the backend IsOperator gate', () => {
    expect(PERMISSIONS.systemMonitoring).toEqual(['approver', 'admin']);
  });
});
