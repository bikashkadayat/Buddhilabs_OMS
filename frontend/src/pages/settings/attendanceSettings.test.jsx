import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import SettingsIndex from './Index';

/**
 * Settings -> Attendance & biometric devices. The page is a thin client over
 * the device API, so what is worth pinning here is what a customer sees:
 * the dashboard columns the brief names, Test Connection's verdict, who may
 * edit, and that the hub reaches the page at all.
 */
const role = { value: 'admin' };
// Shaped like the real hook: the backend role is `role`; `user` is the UI
// object, whose own `role` is a different vocabulary. Mocking `user.role`
// here is how a read-only page for real administrators once passed.
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ role: role.value, user: { role: 'ui-role-not-backend' } }),
}));
vi.mock('../../services/adminService', () => ({
  adminService: { getUsers: vi.fn().mockResolvedValue([]) },
}));

const device = {
  id: 'd1', name: 'Main Gate', device_type_display: 'ZKTeco', location: 'Lobby',
  host: '192.168.1.100', port: 4370, sync_mode: 'pull', sync_interval_display: 'Every 15 minutes',
  connection_status: 'offline', last_sync_attempt_at: '2026-10-07T04:00:00Z',
  last_sync_status: 'failed', last_sync_error: 'No response from 192.168.1.100:4370.',
  attendance_imported: 1200, last_sync_imported: 0, unmapped_users: 3, is_active: true,
};
const api = vi.hoisted(() => ({
  dashboard: vi.fn(), mode: vi.fn(), setMode: vi.fn(), test: vi.fn(), sync: vi.fn(),
  users: vi.fn(), syncLogs: vi.fn(), testUnsaved: vi.fn(), create: vi.fn(),
}));
vi.mock('../../services/biometricDeviceService', () => ({ biometricDeviceService: api }));

// Imported after the mocks so the page binds to them.
const { default: AttendanceSettings } = await import('./Attendance');

beforeEach(() => {
  role.value = 'admin';
  Object.values(api).forEach((fn) => fn.mockReset());
  api.dashboard.mockResolvedValue({ data: { devices: [device], totals: {
    active: 1, online: 0, offline: 1, attendance_imported: 1200, unmapped_users: 3 } } });
  api.mode.mockResolvedValue({ data: { attendance_mode: 'both', effective_mode: 'both' } });
});

describe('Settings -> Attendance & biometric devices', () => {
  it('is reachable from the settings hub', () => {
    render(<MemoryRouter><SettingsIndex /></MemoryRouter>);
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(links).toContain('/settings/attendance');
  });

  it('shows the dashboard: status, last sync, imported, error', async () => {
    render(<MemoryRouter><AttendanceSettings /></MemoryRouter>);
    expect(await screen.findByText('Main Gate')).toBeInTheDocument();
    expect(screen.getByText('Device Offline')).toBeInTheDocument();
    expect(screen.getByText('192.168.1.100:4370')).toBeInTheDocument();
    expect(screen.getAllByText('1200')).toHaveLength(2);        // tile + row
    expect(screen.getByText(/No response from/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /App \+ Biometric/ })).toBeChecked();
  });

  it('reports Test Connection with the device information it read', async () => {
    api.test.mockResolvedValue({ data: {
      ok: true, status: 'online', message: 'Device online.', warnings: [],
      device_info: { serial_number: 'ZK-SN-1', firmware: 'Ver 6.60' },
      users: { count: 42 }, attendance: { count: 9001 }, clock: { drift_seconds: 3 },
      device: { ...device, connection_status: 'online' },
    } });
    render(<MemoryRouter><AttendanceSettings /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /Test/ }));
    expect(await screen.findByText('ZK-SN-1')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    await waitFor(() => expect(api.test).toHaveBeenCalledWith('d1'));
  });

  it('lets HR read but not add, test or change the mode', async () => {
    role.value = 'approver';
    render(<MemoryRouter><AttendanceSettings /></MemoryRouter>);
    expect(await screen.findByText('Main Gate')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add device/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(await screen.findByRole('radio', { name: /App only/ })).toBeDisabled();
  });
});
