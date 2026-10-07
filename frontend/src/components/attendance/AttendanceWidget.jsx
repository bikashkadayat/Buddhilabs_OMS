import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Clock, CheckCircle, Fingerprint, LogIn, LogOut, MapPin, AlertTriangle,
  Building2,
} from 'lucide-react';
import {
  attendanceService, geolocationBlockedReason,
} from '../../services/attendanceService';
import { useAttendancePunch } from '../../hooks/useAttendancePunch';
import LiveLocationMap from './LiveLocationMap';
import { fmtDistance } from '../../utils/geo';

// --- APP-BASED ATTENDANCE: RE-ENABLED ------------------------------------
//
// Self check-in/out and the location capture behind it were commented out in
// this file while the biometric devices were the only sanctioned source. The
// backend never stopped supporting either -- the columns, the validation, the
// geofence and the audit trail were all live the whole time, which is why
// turning this back on is a restoration rather than a rewrite.
//
// Two things are genuinely new:
//   * `location_source` travels with the fix, so a pin records whether it
//     came from satellites or from an IP database;
//   * the buttons respect the tenant's `attendance_mode`, so a customer who
//     records attendance from devices only never sees them.

// Fallback when the server hasn't sent its threshold: fixes worse than this
// (metres) are network/IP estimates, not GPS. We warn but still allow.
const ACCURATE_THRESHOLD_M = 100;

/** "At Office" / "1.2 km away" chip — rendered only when a geofence is configured. */
const GeoChip = ({ distance, within, officeName }) => {
  if (distance == null || within == null) return null;
  return (
    <span className={`att-geo${within ? ' att-geo-in' : ' att-geo-out'}`}
      title={`${fmtDistance(distance)} from ${officeName}`}>
      <Building2 size={12} aria-hidden="true" />
      {within ? `At ${officeName}` : `${fmtDistance(distance)} away`}
    </span>
  );
};

// Compact display of a captured location: address (or coordinates) + accuracy +
// geofence verdict + map link.
const LocationLine = ({ label, address, lat, lng, accuracy, distance, within, officeName, threshold }) => {
  if (!lat || !lng) return null;
  const text = address || `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`;
  const href = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
  const acc = accuracy != null ? `±${Math.round(accuracy)}m` : null;
  const poor = accuracy != null && accuracy > threshold;
  return (
    <div className="att-loc">
      <MapPin size={13} aria-hidden="true" />
      <span className="att-loc-label">{label}:</span>
      <span className="att-loc-text" title={`${text}${acc ? ` (accuracy ${acc})` : ''}`}>{text}</span>
      {acc && <span className={`att-loc-acc${poor ? ' att-loc-acc-poor' : ''}`}>{acc}</span>}
      <GeoChip distance={distance} within={within} officeName={officeName} />
      <a className="att-loc-link" href={href} target="_blank" rel="noopener noreferrer">View on map</a>
    </div>
  );
};

const STATUS_META = {
  present: { label: 'Present', color: 'var(--success)' },
  late: { label: 'Late', color: 'var(--warning)' },
  half_day: { label: 'Half Day', color: '#eab308' },
  absent: { label: 'Absent', color: 'var(--danger)' },
  on_leave: { label: 'On Leave', color: 'var(--brand-blue)' },
  holiday: { label: 'Holiday', color: 'var(--text-muted)' },
};

const Stat = ({ label, value, color }) => (
  <div className="att-stat">
    <div className="att-stat-val" style={{ color }}>{value}</div>
    <div className="att-stat-lbl">{label}</div>
  </div>
);

const AttendanceWidget = () => {
  // Today's record and the check-in/out action come from the same hook the
  // home page uses, so the two can never disagree about where somebody's
  // day stands.
  const {
    today: data, isLoading, punch, pending, locating, error: err,
  } = useAttendancePunch();
  const act = { mutate: punch, isPending: pending };

  // Live biometric today: refetched every minute (and on focus) so the widget
  // reflects the device without a manual reload. The endpoint returns only this
  // employee's own punches.
  const { data: bio } = useQuery({
    queryKey: ['attendance', 'biometric', 'widget'],
    queryFn: () => attendanceService.biometricMe({ days: 30 }),
    refetchInterval: 60000,
    refetchOnWindowFocus: true,
  });

  if (isLoading) return <div className="table-card att-card">Loading attendance…</div>;
  const t = data || {};
  const m = t.month_summary || {};
  const meta = STATUS_META[t.status] || { label: t.status || '—', color: 'var(--text-secondary)' };

  // Prefer live biometric data for today's status/times; fall back to the manual
  // record (or a "link your ID" hint) when the device isn't linked/available.
  const bioReady = bio && bio.configured !== false && bio.found !== false && bio.available !== false;
  const bt = bioReady ? (bio.today || null) : null;
  let live = { officeStart: t.office_start || '10:00', note: null, isLive: false };
  if (bioReady) {
    live.isLive = true;
    live.officeStart = bio.summary?.office_start || live.officeStart;
    if (bt) {
      live.label = bt.late ? 'Late' : 'Present';
      live.color = bt.late ? 'var(--warning)' : 'var(--success)';
      live.in = bt.first || '—';
      live.out = bt.last || '—';
      live.hours = bt.hours != null ? String(bt.hours) : '—';
      live.note = bt.last ? null : 'Checked in — waiting for check-out.';
    } else {
      live.label = 'Not checked in yet';
      live.color = 'var(--text-muted)';
      live.in = '—'; live.out = '—'; live.hours = '—';
      live.note = 'No biometric punch recorded today yet.';
    }
  } else {
    live.label = meta.label;
    live.color = meta.color;
    live.in = t.check_in_local || '—';
    live.out = t.check_out_local || '—';
    live.hours = t.working_hours || '0.00';
    live.note = bio && bio.configured === false
      ? 'Ask HR to link your biometric ID to see live device punches.'
      : null;
  }
  const office = t.office || null;
  const officeName = office?.name || 'Office';
  const threshold = t.max_accuracy_m || ACCURATE_THRESHOLD_M;
  const blocked = geolocationBlockedReason();
  // A tenant on biometric-only never sees the buttons. The server refuses
  // the submission independently — a hidden control is a courtesy, not a
  // control.
  const appAllowed = t.app_check_in_allowed !== false;

  return (
    <div className="table-card att-card">
      <div className="att-head">
        <h3><Clock size={18} /> My Attendance</h3>
        <div className="att-date">B.S. {t.date_bs} · {t.date}</div>
      </div>

      <div className="att-today">
        <div>
          <div className="att-today-lbl" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            Today's status
            {live.isLive && (
              <span title="Live from the biometric device" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-label)', fontWeight: 600, color: 'var(--success)', textTransform: 'none' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--success)', display: 'inline-block' }} /> LIVE
              </span>
            )}
          </div>
          <div className="att-today-status" style={{ color: live.color }}>
            <CheckCircle size={16} /> {live.label}
          </div>
          <div className="att-today-times">
            In: <strong>{live.in}</strong> · Out: <strong>{live.out}</strong> · Hours: <strong>{live.hours}</strong>
            <span className="att-office"> (office starts {live.officeStart})</span>
          </div>
          {live.note && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginTop: 4 }}>{live.note}</div>
          )}
          {/* Where the employee was standing at each end of the day. */}
          <div className="att-today-locations">
            <LocationLine
              label="In" address={t.check_in_address} lat={t.check_in_lat} lng={t.check_in_lng}
              accuracy={t.check_in_accuracy} distance={t.check_in_distance_m}
              within={t.check_in_within_office} officeName={officeName} threshold={threshold}
            />
            <LocationLine
              label="Out" address={t.check_out_address} lat={t.check_out_lat} lng={t.check_out_lng}
              accuracy={t.check_out_accuracy} distance={t.check_out_distance_m}
              within={t.check_out_within_office} officeName={officeName} threshold={threshold}
            />
            {blocked && (
              <div className="att-loc-warn">
                <AlertTriangle size={13} aria-hidden="true" />
                {blocked}
              </div>
            )}
            {!blocked && (() => {
              const worst = Math.max(t.check_in_accuracy || 0, t.check_out_accuracy || 0);
              if (worst <= threshold) return null;
              return (
                <div className="att-loc-warn">
                  <AlertTriangle size={13} aria-hidden="true" />
                  Approximate location (±{Math.round(worst)}m). For an exact location, check in from your phone with GPS/location enabled.
                </div>
              );
            })()}
          </div>
        </div>
        {appAllowed ? (
          <div className="att-actions">
            <button
              type="button"
              className="btn btn-success"
              disabled={!t.can_check_in || act.isPending}
              onClick={() => act.mutate('in')}
            >
              <LogIn size={16} aria-hidden="true" />
              {/* The label says which step is slow. A GPS lock takes a few
                  seconds and a button that just looks stuck gets pressed
                  again, which is how two check-ins get attempted. */}
              {act.isPending
                ? (locating ? 'Getting your location…' : 'Checking in…')
                : 'Check In'}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!t.can_check_out || act.isPending}
              onClick={() => act.mutate('out')}
            >
              <LogOut size={16} aria-hidden="true" />
              {act.isPending
                ? (locating ? 'Getting your location…' : 'Checking out…')
                : 'Check Out'}
            </button>
          </div>
        ) : (
          <div className="att-foot">
            Attendance is recorded from biometric devices for your
            organization.
          </div>
        )}
      </div>
      {err && <div className="att-err" role="alert">{err}</div>}

      {appAllowed && <LiveLocationMap office={office} maxAccuracy={threshold} />}

      {bioReady ? (
        <>
          <div className="att-stats">
            <Stat label="Days present" value={bio.summary?.days_present ?? 0} color="var(--success)" />
            <Stat label="Usually arrives" value={bio.summary?.median_arrival || '—'} color="var(--brand-blue)" />
            <Stat label="Usually leaves" value={bio.summary?.median_departure || '—'} color="var(--brand-blue)" />
            <Stat label="Late days" value={bio.summary?.late_days ?? 0} color="var(--warning)" />
          </div>
          <div className="att-foot">Last 30 days · from the biometric device</div>
        </>
      ) : (
        <>
          <div className="att-stats">
            <Stat label="Present" value={(m.present || 0) + (m.late || 0)} color="var(--success)" />
            <Stat label="On Leave" value={m.on_leave || 0} color="var(--brand-blue)" />
            <Stat label="Absent" value={m.absent || 0} color="var(--danger)" />
            <Stat label="Holidays" value={m.holiday || 0} color="var(--text-muted)" />
          </div>
          <div className="att-foot">This month · present includes late days</div>
        </>
      )}
      <Link to="/my-attendance" className="att-biometric-link"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 10, fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--brand-blue, #2563EB)', textDecoration: 'none' }}>
        <Fingerprint size={15} /> View my biometric attendance →
      </Link>
    </div>
  );
};

export default AttendanceWidget;
