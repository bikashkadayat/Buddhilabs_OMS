import React, { useEffect, useState } from 'react';
import { platformService } from '../../services/platformService';

/**
 * Per-tenant attendance settings, for a platform operator.
 *
 * WHY THIS PANEL EXISTS AT ALL. `OrganizationSettings` has carried
 * `office_lat`, `office_lng`, `office_radius_m`, `max_accuracy_m` and
 * `require_location` since Phase S6. The API to read and write them has
 * existed just as long. **Nothing in the console ever rendered them**, and
 * nothing in the backend ever read them — every attendance setting resolved
 * from one deployment-wide value, so every customer's geofence was measured
 * against a single office.
 *
 * Those two facts are the same fact. A column nobody can set is a column
 * nobody notices is unread. Fixing the read path without this panel would
 * have left the values correct and unsettable.
 *
 * BLANK MEANS INHERIT, on every field here. That is what makes the change
 * safe for the single-tenant deployment: a tenant who sets nothing behaves
 * exactly as it did before, on the platform's own defaults.
 */
const MODES = [
  ['', 'Platform default (both)'],
  ['both', 'Biometric devices and app check-in'],
  ['app_only', 'App check-in only'],
  ['biometric_only', 'Biometric devices only'],
];

const NUMBER_FIELDS = [
  ['office_lat', 'Office latitude', 'e.g. 27.704500. Blank disables the geofence.'],
  ['office_lng', 'Office longitude', 'e.g. 85.307000.'],
  ['office_radius_m', 'Geofence radius (m)', 'How close counts as "at the office". Default 150.'],
  ['max_accuracy_m', 'Worst accepted accuracy (m)', 'A fix wider than this is flagged as approximate. Default 100.'],
];

const AttendancePanel = ({ slug }) => {
  const [row, setRow] = useState(null);
  const [draft, setDraft] = useState({});
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    platformService.settings(slug)
      .then(({ data }) => { if (alive) { setRow(data); setDraft({}); } })
      .catch(() => { if (alive) setError('Settings could not be loaded.'); });
    return () => { alive = false; };
  }, [slug]);

  const val = (field) => (draft[field] !== undefined
    ? draft[field] : (row?.[field] ?? ''));

  const set = (field) => (event) => {
    setDraft((d) => ({ ...d, [field]: event.target.value }));
    setNotice(null);
  };

  const save = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      // An empty field is sent as null, not as "". These columns are
      // nullable precisely so that "inherit the platform default" is
      // expressible, and "" would be a value.
      const body = {};
      Object.entries(draft).forEach(([key, value]) => {
        if (key === 'require_location') {
          body[key] = value === '' ? null : value === 'yes';
        } else if (key === 'attendance_mode') {
          body[key] = value;          // "" is this field's own "inherit"
        } else {
          body[key] = value === '' ? null : value;
        }
      });
      const { data } = await platformService.setSettings(slug, body);
      setRow(data); setDraft({});
      setNotice('Saved. It applies to this tenant on their next request.');
    } catch (err) {
      setError(err?.response?.data?.detail
        || 'That could not be saved. Check the coordinates are numbers.');
    } finally { setBusy(false); }
  };

  if (!row) {
    return (
      <section className="br-card">
        <h2 className="br-h">Attendance</h2>
        <p className="br-hint">{error || 'Loading…'}</p>
      </section>
    );
  }

  const dirty = Object.keys(draft).length > 0;

  return (
    <section className="br-card">
      <h2 className="br-h">Attendance</h2>
      <p className="br-lede">
        How this customer records attendance, and where their office is.
        Leave a field blank to inherit the platform default.
      </p>

      {error && <div className="br-alert br-alert-bad" role="alert">{error}</div>}
      {notice && <div className="br-alert br-alert-ok" role="status">{notice}</div>}

      <div className="br-row">
        <label className="br-label" htmlFor="att-mode">Attendance mode</label>
        <div className="br-field">
          <select
            id="att-mode"
            className="br-input br-input-wide"
            value={val('attendance_mode')}
            onChange={set('attendance_mode')}
          >
            {MODES.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <p className="br-hint">
          <strong>Biometric devices only</strong> hides the Check In / Check
          Out buttons from this customer&apos;s staff and refuses an app
          check-in. <strong>App check-in only</strong> keeps storing device
          punches as evidence but stops deriving attendance from them, so an
          employee&apos;s own check-in is never overwritten.
        </p>
      </div>

      <div className="br-row">
        <label className="br-label" htmlFor="att-office-name">Office name</label>
        <div className="br-field">
          <input
            id="att-office-name"
            className="br-input br-input-wide"
            type="text"
            value={val('office_name')}
            onChange={set('office_name')}
            placeholder="Head Office"
          />
        </div>
        <p className="br-hint">
          Shown to their staff as &quot;At Head Office&quot; when a check-in
          falls inside the radius.
        </p>
      </div>

      {NUMBER_FIELDS.map(([field, label, hint]) => (
        <div className="br-row" key={field}>
          <label className="br-label" htmlFor={`att-${field}`}>{label}</label>
          <div className="br-field">
            <input
              id={`att-${field}`}
              className="br-input"
              type="text"
              inputMode="decimal"
              value={val(field)}
              onChange={set(field)}
              aria-describedby={`att-${field}-hint`}
            />
          </div>
          <p className="br-hint" id={`att-${field}-hint`}>{hint}</p>
        </div>
      ))}

      <div className="br-row">
        <label className="br-label" htmlFor="att-require">
          Require a location to check in
        </label>
        <div className="br-field">
          <select
            id="att-require"
            className="br-input"
            value={(() => {
              const v = draft.require_location !== undefined
                ? draft.require_location : row.require_location;
              if (v === '' || v === null || v === undefined) return '';
              return v === true || v === 'yes' ? 'yes' : 'no';
            })()}
            onChange={set('require_location')}
          >
            <option value="">Platform default</option>
            <option value="yes">Yes — refuse a check-in with no location</option>
            <option value="no">No — allow it, and record no coordinates</option>
          </select>
        </div>
        <p className="br-hint">
          Turn this off for a customer whose staff work on desktops without
          GPS. Their records will have no pins, which is better than their
          staff being unable to check in.
        </p>
      </div>

      <div className="br-actions">
        <button
          type="button"
          className="br-save"
          onClick={save}
          disabled={busy || !dirty}
        >
          {busy ? 'Saving…' : 'Save attendance settings'}
        </button>
        {dirty && (
          <button
            type="button"
            className="br-cancel"
            onClick={() => { setDraft({}); setNotice(null); }}
            disabled={busy}
          >
            Discard changes
          </button>
        )}
      </div>
    </section>
  );
};

export default AttendancePanel;
