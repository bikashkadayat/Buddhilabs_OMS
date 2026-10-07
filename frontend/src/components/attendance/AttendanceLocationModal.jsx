import React from 'react';
import {
  MapPin, Building2, AlertTriangle, LogIn, LogOut, MoveHorizontal,
} from 'lucide-react';
import Modal from '../common/Modal';
import { osmEmbedSrc, googleMapsHref, fmtDistance } from '../../utils/geo';

// Fixes worse than this (metres) are network/IP estimates rather than GPS.
const ACCURATE_THRESHOLD_M = 100;

const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : null;

/**
 * One pinned location: where the employee was standing when they pressed the
 * button. Shows the map, the resolved address, how precise the fix was, and how
 * far it landed from the office.
 */
const PinPanel = ({ icon, label, time, address, lat, lng, accuracy, distance, within, source }) => {
  if (!lat || !lng) {
    return (
      <div className="att-pin">
        <div className="att-pin-head">{icon}<span>{label}</span></div>
        <div className="att-pin-empty">
          No location recorded{time ? ` — ${label.toLowerCase()} at ${time}` : ''}.
        </div>
      </div>
    );
  }
  const src = osmEmbedSrc({ lat: Number(lat), lng: Number(lng), accuracy, aspect: 1.6 });
  const poor = accuracy != null && accuracy > ACCURATE_THRESHOLD_M;

  return (
    <div className="att-pin">
      <div className="att-pin-head">
        {icon}
        <span>{label}</span>
        {time && <span className="att-pin-time">{time}</span>}
      </div>

      <iframe
        className="att-pin-frame"
        title={`${label} location on a map`}
        src={src}
        loading="lazy"
        referrerPolicy="no-referrer-when-downgrade"
      />

      <div className="att-pin-body">
        <div className="att-loc" style={{ margin: 0 }}>
          <MapPin size={13} aria-hidden="true" />
          <span className="att-loc-text" title={address || ''}>
            {address || `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`}
          </span>
        </div>

        <div className="att-pin-meta">
          {accuracy != null && (
            <span className={`att-loc-acc${poor ? ' att-loc-acc-poor' : ''}`}>
              ±{Math.round(accuracy)}m
            </span>
          )}
          {distance != null && within != null && (
            <span className={`att-geo${within ? ' att-geo-in' : ' att-geo-out'}`}>
              <Building2 size={12} aria-hidden="true" />
              {within ? 'At office' : `${fmtDistance(distance)} away`}
            </span>
          )}
          <a
            className="att-loc-link"
            href={googleMapsHref(lat, lng)}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open in Google Maps
          </a>
        </div>

        <div className="att-pin-coords">{Number(lat).toFixed(6)}, {Number(lng).toFixed(6)}</div>
        {/* How the fix was obtained. An accuracy figure alone does not say
            whether this came from satellites or from an IP database, and
            that is the difference between evidence and an estimate. */}
        {source && <div className="att-pin-source">{source}</div>}

        {poor && (
          <div className="att-loc-warn">
            <AlertTriangle size={13} aria-hidden="true" />
            Approximate fix — recorded from a device without GPS, so treat the pin as a rough area.
          </div>
        )}
      </div>
    </div>
  );
};

/**
 * Where an employee checked in and out, for admin / HR / department heads.
 *
 * Both pins live in one dialog so the two ends of the day can be compared at a
 * glance — the common question is not "where was this pin?" but "did they start
 * and finish in the same place?".
 */
const AttendanceLocationModal = ({ record, onClose }) => {
  if (!record) return null;
  const title = `${record.employee_name || 'Employee'} · ${record.date}`;

  return (
    <Modal title={title} onClose={onClose} width={900} ariaLabel={`Check-in and check-out locations for ${title}`}>
      <div className="att-pin-grid">
        <PinPanel
          icon={<LogIn size={14} aria-hidden="true" />}
          label="Check-in"
          time={fmtTime(record.check_in)}
          address={record.check_in_address}
          lat={record.check_in_lat}
          lng={record.check_in_lng}
          accuracy={record.check_in_accuracy}
          distance={record.check_in_distance_m}
          within={record.check_in_within_office}
          source={record.check_in_location_source_display}
        />
        <PinPanel
          icon={<LogOut size={14} aria-hidden="true" />}
          label="Check-out"
          time={fmtTime(record.check_out)}
          address={record.check_out_address}
          lat={record.check_out_lat}
          lng={record.check_out_lng}
          accuracy={record.check_out_accuracy}
          distance={record.check_out_distance_m}
          within={record.check_out_within_office}
          source={record.check_out_location_source_display}
        />
      </div>
      {/* THE QUESTION THE TWO PINS ARE ACTUALLY ASKED. "Did they start and
          finish in the same place?" is answered by the distance between
          them, not by reading two coordinate pairs and doing the arithmetic
          in your head. Computed on the server (`travel_distance_m`) so the
          table, this dialog and any export agree. */}
      {record.travel_distance_m != null && (
        <div className="att-pin-travel">
          <MoveHorizontal size={14} aria-hidden="true" />
          <span>
            <strong>{fmtDistance(record.travel_distance_m)}</strong> between
            check-in and check-out
          </span>
          <span className="att-pin-travel-note">
            {record.travel_distance_m <= 50
              ? 'Same place, within GPS error.'
              : 'Different places — check the two pins above.'}
          </span>
        </div>
      )}
    </Modal>
  );
};

export default AttendanceLocationModal;
