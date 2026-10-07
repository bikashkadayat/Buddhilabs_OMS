import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * App-based attendance, from the employee's side.
 *
 * WHAT THIS GUARDS. The check-in/check-out buttons and the GPS capture behind
 * them were commented out in this component for several phases while
 * biometric devices were the only sanctioned source — the backend never
 * stopped supporting them. A feature that can be switched off by deleting a
 * few lines of JSX, with no test to notice, is a feature that will be.
 *
 * So these tests assert the BEHAVIOUR a browser performs: the location is
 * captured from the device, it is sent with the request, the employee is
 * told which step is slow, a refusal is explained, and a tenant that records
 * attendance from hardware never sees the buttons at all.
 */
const checkIn = vi.fn();
const checkOut = vi.fn();
const getCurrentLocation = vi.fn();
const geolocationBlockedReason = vi.fn(() => null);

vi.mock('../../services/attendanceService', () => ({
  attendanceService: {
    today: (...a) => mockToday(...a),
    checkIn: (...a) => checkIn(...a),
    checkOut: (...a) => checkOut(...a),
    biometricMe: () => Promise.resolve({ configured: false }),
  },
  getCurrentLocation: (...a) => getCurrentLocation(...a),
  geolocationBlockedReason: (...a) => geolocationBlockedReason(...a),
  classifyFix: (fix) => {
    if (!fix || fix.accuracy == null) return 'unknown';
    if (fix.accuracy <= 100) return 'gps';
    if (fix.accuracy <= 2000) return 'network';
    return 'ip';
  },
}));

vi.mock('./LiveLocationMap', () => ({ default: () => <div data-testid="live-map" /> }));

let mockToday = vi.fn();

import AttendanceWidget from './AttendanceWidget';

const TODAY = {
  date: '2026-10-06',
  status: 'absent',
  can_check_in: true,
  can_check_out: false,
  app_check_in_allowed: true,
  attendance_mode: 'both',
  working_hours: '0.00',
  month_summary: {},
  office: { name: 'ABC Office', lat: 27.7045, lng: 85.307, radius_m: 150 },
  max_accuracy_m: 100,
  office_start: '10:00',
};

const wrap = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AttendanceWidget /></MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  geolocationBlockedReason.mockReturnValue(null);
  mockToday = vi.fn().mockResolvedValue(TODAY);
  getCurrentLocation.mockResolvedValue({
    latitude: 27.70455, longitude: 85.30705, accuracy: 11,
  });
  checkIn.mockResolvedValue({});
  checkOut.mockResolvedValue({});
});

describe('app-based check-in', () => {
  it('offers the buttons when the tenant allows app attendance', async () => {
    wrap();
    expect(await screen.findByRole('button', { name: /check in/i }))
      .toBeEnabled();
  });

  it('captures the device location and sends it with the request', async () => {
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /check in/i }));
    await waitFor(() => expect(checkIn).toHaveBeenCalled());
    expect(getCurrentLocation).toHaveBeenCalled();
    expect(checkIn).toHaveBeenCalledWith({
      latitude: 27.70455,
      longitude: 85.30705,
      accuracy: 11,
      location_source: 'gps',
    });
  });

  it('labels a coarse fix as a network one rather than calling it GPS', async () => {
    getCurrentLocation.mockResolvedValue({
      latitude: 27.70455, longitude: 85.30705, accuracy: 900,
    });
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /check in/i }));
    await waitFor(() => expect(checkIn).toHaveBeenCalled());
    expect(checkIn.mock.calls[0][0].location_source).toBe('network');
  });

  it('never offers a way to type a location', async () => {
    // The pin is evidence precisely because the employee cannot author it.
    const { container } = wrap();
    await screen.findByRole('button', { name: /check in/i });
    const typed = Array.from(container.querySelectorAll('input, textarea'))
      .filter((el) => /lat|lng|long|location|address|place/i.test(
        `${el.name} ${el.id} ${el.placeholder} ${el.getAttribute('aria-label') || ''}`));
    expect(typed).toEqual([]);
  });

  it('still checks in when the device cannot produce a fix', async () => {
    // The SERVER decides whether a location-less check-in is acceptable,
    // per the tenant's `require_location`. The client does not pre-empt it,
    // or an employee on a desktop is stuck with no explanation.
    getCurrentLocation.mockResolvedValue(null);
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /check in/i }));
    await waitFor(() => expect(checkIn).toHaveBeenCalledWith({}));
  });

  it('explains a refusal in the words the server used', async () => {
    checkIn.mockRejectedValue({
      response: { data: { detail: 'Location is required to check in.' } },
    });
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /check in/i }));
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Location is required to check in.');
  });

  it('warns when the browser cannot provide a location at all', async () => {
    geolocationBlockedReason.mockReturnValue(
      'Location needs a secure (HTTPS) connection.');
    wrap();
    expect(await screen.findByText(/secure \(HTTPS\) connection/i))
      .toBeInTheDocument();
  });
});

describe('check-out', () => {
  it('is offered once checked in, and captures its own location', async () => {
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY, can_check_in: false, can_check_out: true,
      check_in_local: '09:58',
    });
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /check out/i }));
    await waitFor(() => expect(checkOut).toHaveBeenCalled());
    expect(checkOut.mock.calls[0][0]).toMatchObject({ location_source: 'gps' });
  });
});

describe('attendance mode', () => {
  it('hides the buttons entirely for a biometric-only tenant', async () => {
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY, can_check_in: false, app_check_in_allowed: false,
      attendance_mode: 'biometric_only',
    });
    wrap();
    expect(await screen.findByText(/recorded from biometric devices/i))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /check in/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /check out/i })).toBeNull();
  });

  it('and does not render the live map either', async () => {
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY, app_check_in_allowed: false,
    });
    wrap();
    await screen.findByText(/recorded from biometric devices/i);
    expect(screen.queryByTestId('live-map')).toBeNull();
  });

  it('treats a server that says nothing as permitting app check-in', async () => {
    // Older payloads have no `app_check_in_allowed`. Defaulting to hidden
    // would switch the feature off for every client during a rolling deploy.
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY, app_check_in_allowed: undefined,
    });
    wrap();
    expect(await screen.findByRole('button', { name: /check in/i }))
      .toBeInTheDocument();
  });
});

describe('the captured location, on screen', () => {
  it('shows where each end of the day happened, with a map link', async () => {
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY,
      can_check_in: false,
      check_in_local: '09:58',
      check_in_lat: '27.704550', check_in_lng: '85.307050',
      check_in_accuracy: 12, check_in_address: 'ABC Office, Kupondole',
      check_in_distance_m: 8.2, check_in_within_office: true,
    });
    wrap();
    expect(await screen.findByText('ABC Office, Kupondole')).toBeInTheDocument();
    expect(screen.getByText('At ABC Office')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /view on map/i });
    expect(link).toHaveAttribute(
      'href', expect.stringContaining('27.704550,85.307050'));
    expect(link).toHaveAttribute('href', expect.stringContaining('google.com/maps'));
  });

  it('flags an approximate fix instead of presenting it as exact', async () => {
    mockToday = vi.fn().mockResolvedValue({
      ...TODAY, can_check_in: false,
      check_in_lat: '27.704550', check_in_lng: '85.307050',
      check_in_accuracy: 2400,
    });
    wrap();
    expect(await screen.findByText(/Approximate location/i)).toBeInTheDocument();
  });
});
