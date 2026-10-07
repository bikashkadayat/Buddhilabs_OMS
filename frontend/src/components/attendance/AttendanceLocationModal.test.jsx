import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import AttendanceLocationModal from './AttendanceLocationModal';

const record = {
  id: 'a1',
  employee_name: 'Sita Sharma',
  date: '2026-07-23',
  check_in: '2026-07-23T04:10:00Z',
  check_in_lat: '27.704550',
  check_in_lng: '85.307050',
  check_in_accuracy: 12,
  check_in_address: 'NIF Office, Kupondole',
  check_in_distance_m: 8.2,
  check_in_within_office: true,
  check_out: '2026-07-23T12:05:00Z',
  check_out_lat: '27.715400',
  check_out_lng: '85.312300',
  check_out_accuracy: 480,
  check_out_address: 'Thamel, Kathmandu',
  check_out_distance_m: 1319.6,
  check_out_within_office: false,
};

const mapFrames = () =>
  Array.from(document.querySelectorAll('iframe')).map((f) => f.getAttribute('src'));

describe('AttendanceLocationModal — where the employee checked in and out', () => {
  it('pins both ends of the day on their own maps', () => {
    render(<AttendanceLocationModal record={record} onClose={vi.fn()} />);

    expect(screen.getByText('Check-in')).toBeInTheDocument();
    expect(screen.getByText('Check-out')).toBeInTheDocument();
    expect(screen.getByText('NIF Office, Kupondole')).toBeInTheDocument();
    expect(screen.getByText('Thamel, Kathmandu')).toBeInTheDocument();

    const [checkIn, checkOut] = mapFrames();
    expect(checkIn).toContain('marker=27.70455%2C85.30705');
    expect(checkOut).toContain('marker=27.7154%2C85.3123');
  });

  it('shows the geofence verdict so a manager sees who was off-site', () => {
    render(<AttendanceLocationModal record={record} onClose={vi.fn()} />);

    expect(screen.getByText('At office')).toBeInTheDocument();
    expect(screen.getByText('1.32 km away')).toBeInTheDocument();
  });

  it('flags a fix too imprecise to trust as an exact spot', () => {
    render(<AttendanceLocationModal record={record} onClose={vi.fn()} />);

    expect(screen.getByText('±480m')).toBeInTheDocument();
    expect(screen.getByText(/approximate fix/i)).toBeInTheDocument();
    // The precise check-in must NOT carry that warning.
    expect(screen.getAllByText(/approximate fix/i)).toHaveLength(1);
  });

  it('says so plainly when a check-out has no location, instead of an empty map', () => {
    const noCheckout = {
      ...record,
      check_out: null, check_out_lat: null, check_out_lng: null,
      check_out_address: '', check_out_accuracy: null,
      check_out_distance_m: null, check_out_within_office: null,
    };
    render(<AttendanceLocationModal record={noCheckout} onClose={vi.fn()} />);

    expect(screen.getByText(/no location recorded/i)).toBeInTheDocument();
    expect(mapFrames()).toHaveLength(1); // only the check-in map
  });

  it('closes on the × button', async () => {
    const onClose = vi.fn();
    render(<AttendanceLocationModal record={record} onClose={onClose} />);

    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders nothing without a record', () => {
    const { container } = render(<AttendanceLocationModal record={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});

// --- app-based attendance phase: the distance between the two pins -------
describe('AttendanceLocationModal — distance between the pins', () => {
  it('answers the question the two pins are actually asked', () => {
    // "Did they start and finish in the same place?" should not require
    // reading two coordinate pairs and doing the arithmetic in your head.
    render(
      <AttendanceLocationModal
        record={{ ...record, travel_distance_m: 1319.6 }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/between\s+check-in and check-out/i))
      .toBeInTheDocument();
    expect(screen.getByText('1.32 km')).toBeInTheDocument();
    expect(screen.getByText(/Different places/i)).toBeInTheDocument();
  });

  it('calls a short hop the same place, within GPS error', () => {
    render(
      <AttendanceLocationModal
        record={{ ...record, travel_distance_m: 14 }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Same place, within GPS error/i))
      .toBeInTheDocument();
  });

  it('says nothing at all before the employee has checked out', () => {
    render(
      <AttendanceLocationModal
        record={{ ...record, check_out: null, check_out_lat: null,
                  check_out_lng: null, travel_distance_m: null }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/between check-in and check-out/i)).toBeNull();
  });

  it('shows how each fix was obtained', () => {
    // An accuracy figure alone does not say whether a pin came from
    // satellites or from an IP database, and that is the difference between
    // evidence and an estimate.
    render(
      <AttendanceLocationModal
        record={{ ...record,
                  check_in_location_source_display: 'Device GPS',
                  check_out_location_source_display: 'Wi-Fi / cell network' }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('Device GPS')).toBeInTheDocument();
    expect(screen.getByText('Wi-Fi / cell network')).toBeInTheDocument();
  });
});
