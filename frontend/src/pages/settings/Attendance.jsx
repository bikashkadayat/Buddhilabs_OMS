import React, { useCallback, useEffect, useState } from 'react';
import {
  Fingerprint, RefreshCw, PlugZap, Pencil, Trash2, Users, ScrollText, Plus, Wand2,
} from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { useAuth } from '../../hooks/useAuth';
import RateThis from '../../components/help/RateThis';
import { adminService } from '../../services/adminService';
import { biometricDeviceService as api } from '../../services/biometricDeviceService';

/**
 * Settings -> Attendance: the attendance mode and the organization's
 * biometric devices (the legacy NIF integration, self-service).
 *
 * THE SERVER DOES THE TALKING. "Test connection" and "Sync now" are run by
 * the platform, not by this browser, so the address entered has to be
 * reachable from where the platform runs. The page says so next to the
 * address field, because "it works from my office PC" is the first thing
 * anybody will try.
 *
 * Administrators edit; HR (approver role) sees the dashboard, the logs and
 * the mapping queue. The server enforces that independently of what this
 * page shows.
 */
const DEVICE_TYPES = [
  ['zkteco', 'ZKTeco'],
  ['zk_compatible', 'ZK-compatible (eSSL, Realtime, Identix)'],
];
const INTERVALS = [[5, 'Every 5 minutes'], [15, 'Every 15 minutes'],
  [30, 'Every 30 minutes'], [0, 'Manual sync only']];
const MODES = [
  ['app_only', 'App only', 'Employees check in from the web or mobile app. Device punches are kept but do not count.'],
  ['biometric_only', 'Biometric only', 'Attendance comes from devices. The app check-in button is hidden.'],
  ['both', 'App + Biometric', 'Both count. Earliest valid check-in and latest valid check-out win; both are kept.'],
];
const BLANK = {
  name: '', device_type: 'zkteco', host: '', port: 4370, location: '',
  is_active: true, sync_interval_minutes: 15, comm_key: '',
};

const STATUS_TONE = {
  online: 'is-good', offline: 'is-bad', unknown: 'is-mute',
  success: 'is-good', partial: 'is-warn', failed: 'is-bad', skipped: 'is-mute', started: 'is-info',
};
const Badge = ({ value, label }) => (
  <span className={`ui-badge ${STATUS_TONE[value] || 'is-mute'}`}>{label || value || '—'}</span>
);

const when = (iso) => (iso ? new Date(iso).toLocaleString('en-GB', {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
}) : 'Never');

const errorText = (err, fallback) => {
  const data = err?.response?.data;
  if (!data) return fallback;
  if (typeof data === 'string') return fallback;
  if (data.detail) return data.detail;
  const first = Object.entries(data)[0];
  return first ? `${first[0]}: ${[].concat(first[1]).join(' ')}` : fallback;
};

// ---------------------------------------------------------------------------
const ModeCard = ({ canEdit }) => {
  const [mode, setMode] = useState(null);
  const [saved, setSaved] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    api.mode().then(({ data }) => {
      setMode(data.attendance_mode || data.effective_mode);
      setSaved(data);
    }).catch(() => setSaved({ error: true }));
  }, []);

  const save = async (value) => {
    setMode(value); setNotice(null);
    try {
      const { data } = await api.setMode(value);
      setSaved(data);
      setNotice('Saved. It applies from the next check-in or sync.');
    } catch (err) {
      setNotice(errorText(err, 'The mode could not be saved.'));
    }
  };

  if (!saved) return <section className="br-card"><Skeleton rows={2} /></section>;
  return (
    <section className="br-card" aria-labelledby="att-mode-h">
      <h2 className="br-h" id="att-mode-h">Attendance mode</h2>
      <p className="br-lede">How your organization takes attendance.</p>
      <div role="radiogroup" aria-labelledby="att-mode-h" className="bd-modes">
        {MODES.map(([value, title, body]) => (
          <label key={value} className={`bd-mode ${mode === value ? 'is-on' : ''}`}>
            <input type="radio" name="attendance-mode" value={value}
              checked={mode === value} disabled={!canEdit}
              onChange={() => save(value)} />
            <span className="bd-mode-title">{title}</span>
            <span className="bd-mode-body">{body}</span>
          </label>
        ))}
      </div>
      {!canEdit && <p className="br-hint">Only an administrator can change the mode.</p>}
      {notice && <p className="br-hint" role="status">{notice}</p>}
    </section>
  );
};

// ---------------------------------------------------------------------------
const TestResult = ({ result }) => {
  if (!result) return null;
  const info = result.device_info || {};
  return (
    <div className={`br-alert ${result.ok ? 'br-alert-ok' : 'br-alert-bad'}`} role="status">
      <strong>{result.ok ? 'Device online' : 'Device offline'}</strong> — {result.message}
      {result.ok && (
        <dl className="bd-facts">
          {info.device_name && (<><dt>Model</dt><dd>{info.device_name}</dd></>)}
          {info.serial_number && (<><dt>Serial</dt><dd>{info.serial_number}</dd></>)}
          {info.firmware && (<><dt>Firmware</dt><dd>{info.firmware}</dd></>)}
          {info.platform && (<><dt>Platform</dt><dd>{info.platform}</dd></>)}
          <dt>Users enrolled</dt><dd>{result.users?.count ?? '—'}</dd>
          <dt>Attendance logs</dt>
          <dd>
            {result.attendance?.count ?? result.sizes?.records ?? '—'}
            {result.attendance?.latest && ` (latest ${when(result.attendance.latest)})`}
          </dd>
          {result.clock?.drift_seconds != null && (
            <><dt>Clock drift</dt><dd>{result.clock.drift_seconds}s</dd></>
          )}
        </dl>
      )}
      {(result.warnings || []).map((w) => <p key={w} className="br-hint br-warn">{w}</p>)}
    </div>
  );
};

// ---------------------------------------------------------------------------
const DeviceForm = ({ initial, onSaved, onCancel }) => {
  const editing = Boolean(initial?.id);
  const [form, setForm] = useState(() => ({
    ...BLANK, ...(initial || {}), comm_key: '',
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const set = (field) => (event) => {
    const { type, checked, value } = event.target;
    setForm((f) => ({ ...f, [field]: type === 'checkbox' ? checked : value }));
  };

  const body = () => {
    const out = {
      name: form.name.trim(), device_type: form.device_type, host: form.host.trim(),
      port: Number(form.port) || 4370, location: form.location,
      is_active: form.is_active, sync_interval_minutes: Number(form.sync_interval_minutes),
    };
    // Blank leaves an existing key alone; a number replaces it.
    if (form.comm_key !== '') out.comm_key = Number(form.comm_key);
    return out;
  };

  const test = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      const b = body();
      const { data } = await api.testUnsaved({
        host: b.host, port: b.port, device_type: b.device_type,
        comm_key: b.comm_key ?? 0,
      });
      setResult(data);
    } catch (err) {
      setError(errorText(err, 'The connection test could not be run.'));
    } finally { setBusy(false); }
  };

  const save = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      const { data } = editing
        ? await api.update(initial.id, body())
        : await api.create(body());
      onSaved(data, editing);
    } catch (err) {
      setError(errorText(err, 'The device could not be saved.'));
    } finally { setBusy(false); }
  };

  return (
    <section className="br-card" aria-labelledby="bd-form-h">
      <h2 className="br-h" id="bd-form-h">{editing ? `Edit ${initial.name}` : 'Add device'}</h2>
      <form onSubmit={save} className="bd-form">
        <label className="br-label" htmlFor="bd-name">Device name</label>
        <input id="bd-name" className="br-input br-input-wide" value={form.name}
          onChange={set('name')} placeholder="Main Gate" required />

        <label className="br-label" htmlFor="bd-type">Device type</label>
        <select id="bd-type" className="br-input" value={form.device_type} onChange={set('device_type')}>
          {DEVICE_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>

        <label className="br-label" htmlFor="bd-host">Device IP address</label>
        <input id="bd-host" className="br-input" value={form.host} onChange={set('host')}
          placeholder="192.168.1.100" required aria-describedby="bd-host-hint" />
        <p className="br-hint" id="bd-host-hint">
          The platform connects to this address, not your browser — it must be
          reachable from the server (same network, port-forward, or VPN).
        </p>

        <label className="br-label" htmlFor="bd-port">Port</label>
        <input id="bd-port" className="br-input" type="number" min="1" max="65535"
          value={form.port} onChange={set('port')} />

        <label className="br-label" htmlFor="bd-loc">Location</label>
        <input id="bd-loc" className="br-input br-input-wide" value={form.location}
          onChange={set('location')} placeholder="Ground floor, reception" />

        <label className="br-label" htmlFor="bd-int">Auto sync</label>
        <select id="bd-int" className="br-input" value={form.sync_interval_minutes}
          onChange={set('sync_interval_minutes')}>
          {INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>

        <label className="br-label" htmlFor="bd-key">Communication key</label>
        <input id="bd-key" className="br-input" type="number" min="0" max="999999"
          value={form.comm_key} onChange={set('comm_key')}
          placeholder={editing && initial.has_comm_key ? '•••••• (set)' : '0 if none'} />

        <label className="bd-check">
          <input type="checkbox" checked={form.is_active} onChange={set('is_active')} />
          {' '}Active
        </label>

        {editing && initial.hardware_serial && (
          <p className="br-hint bd-check">
            Pinned to the device with serial <strong>{initial.hardware_serial}</strong>.
            Syncs from any other device at this address are refused.{' '}
            <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
              onClick={async () => {
                setBusy(true); setError(null);
                try {
                  const { data } = await api.resetIdentity(initial.id);
                  onSaved(data, true);
                } catch (err) {
                  setError(errorText(err, 'The identity could not be reset.'));
                } finally { setBusy(false); }
              }}>
              Replaced the device? Reset identity
            </button>
          </p>
        )}
        {error && <div className="br-alert br-alert-bad" role="alert">{error}</div>}
        <TestResult result={result} />

        <div className="br-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={test}
            disabled={busy || !form.host}>
            <PlugZap size={14} aria-hidden="true" /> Test connection
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
            {editing ? 'Save changes' : 'Add device'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </section>
  );
};

// ---------------------------------------------------------------------------
const UsersPanel = ({ device, canMap, onChanged }) => {
  const [rows, setRows] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [filter, setFilter] = useState('false');
  const [proposal, setProposal] = useState(null);
  const [notice, setNotice] = useState(null);
  const [choice, setChoice] = useState({});

  const load = useCallback(() => api.users(device.id, filter === 'all' ? undefined : filter)
    .then(({ data }) => setRows(data)).catch(() => setRows([])), [device.id, filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (canMap) adminService.getUsers().then(setEmployees).catch(() => setEmployees([]));
  }, [canMap]);

  const map = async (row) => {
    setNotice(null);
    try {
      await api.map(row.id, choice[row.id]);
      setNotice(`Device user ${row.device_user_id} mapped.`);
      load(); onChanged();
    } catch (err) { setNotice(errorText(err, 'That mapping could not be saved.')); }
  };

  const autoMatch = async (apply) => {
    setNotice(null);
    try {
      const { data } = await api.autoMatch(device.id, apply);
      if (apply) {
        setProposal(null);
        setNotice(`${data.matched.length} device user(s) mapped by employee ID. `
          + 'Past punches are not re-attributed automatically — use backfill if needed.');
        load(); onChanged();
      } else {
        setProposal(data);
      }
    } catch (err) { setNotice(errorText(err, 'Auto-match could not run.')); }
  };

  return (
    <section className="br-card" aria-labelledby="bd-users-h">
      <h2 className="br-h" id="bd-users-h">Device users — {device.name}</h2>
      <p className="br-lede">
        Each person enrolled on the device has a device user ID. Map it to the
        employee it belongs to; punches from unmapped IDs are stored but not
        counted until they are mapped.
      </p>
      <div className="br-actions">
        <select className="br-input" value={filter} onChange={(e) => setFilter(e.target.value)}
          aria-label="Show">
          <option value="false">Unmatched users</option>
          <option value="true">Mapped users</option>
          <option value="all">All users</option>
        </select>
        {canMap && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => autoMatch(false)}>
            <Wand2 size={14} aria-hidden="true" /> Auto match by employee ID
          </button>
        )}
      </div>
      {notice && <p className="br-hint" role="status">{notice}</p>}

      {proposal && (
        <div className="br-alert br-alert-ok" role="status">
          {proposal.matched.length === 0
            ? 'No device user ID exactly matches an employee\'s biometric ID or employee ID.'
            : `${proposal.matched.length} exact match(es):`}
          <ul>
            {proposal.matched.map((m) => (
              <li key={m.mapping_id}>
                Device user {m.device_user_id} → {m.user_name} ({m.matched_on.replace('_', ' ')})
              </li>
            ))}
            {proposal.skipped.map((m) => (
              <li key={`s-${m.mapping_id}`}>Skipped {m.device_user_id}: {m.reason}</li>
            ))}
          </ul>
          {proposal.matched.length > 0 && (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => autoMatch(true)}>
              Apply {proposal.matched.length} mapping(s)
            </button>
          )}
        </div>
      )}

      {rows === null ? <Skeleton rows={3} /> : rows.length === 0 ? (
        <EmptyState
          variant={filter === 'false' ? 'cleared' : 'first'}
          title={filter === 'false' ? 'Every device user is mapped.' : 'No users read from this device yet.'}
          body={filter === 'false'
            ? 'Punches from every enrolled person are counted. Sync the device to pick up anyone newly enrolled.'
            : 'Press Sync now on the device — it reads everyone enrolled on it, then you map each one here.'} />
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Device users</caption>
            <thead><tr>
              <th scope="col">Device user ID</th><th scope="col">Name on device</th>
              <th scope="col">Employee</th><th scope="col">Punches</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.device_user_id}</td>
                  <td>{r.device_name || '—'}</td>
                  <td>
                    {r.user_detail ? `${r.user_detail.full_name} (${r.user_detail.employee_id || '—'})`
                      : canMap ? (
                        <span className="br-field">
                          <select className="br-input" value={choice[r.id] || ''}
                            aria-label={`Employee for device user ${r.device_user_id}`}
                            onChange={(e) => setChoice((c) => ({ ...c, [r.id]: e.target.value }))}>
                            <option value="">Choose employee…</option>
                            {employees.map((u) => (
                              <option key={u.id} value={u.id}>
                                {`${u.first_name || ''} ${u.last_name || ''}`.trim() || u.email}
                              </option>
                            ))}
                          </select>
                          <button type="button" className="btn btn-primary btn-xs"
                            disabled={!choice[r.id]} onClick={() => map(r)}>Map</button>
                        </span>
                      ) : <Badge value="unknown" label="Unmapped" />}
                  </td>
                  <td>{r.punch_count ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

const LogsPanel = ({ device }) => {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    api.syncLogs(device.id).then(({ data }) => setRows(data)).catch(() => setRows([]));
  }, [device.id, device.last_sync_attempt_at]);
  return (
    <section className="br-card" aria-labelledby="bd-logs-h">
      <h2 className="br-h" id="bd-logs-h">Sync history — {device.name}</h2>
      {rows === null ? <Skeleton rows={3} /> : rows.length === 0 ? (
        <EmptyState title="This device hasn't been synced yet."
          body="Every connection test and every sync — scheduled or manual — is listed here, with what it imported and any error." />
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Sync history</caption>
            <thead><tr>
              <th scope="col">When</th><th scope="col">Type</th><th scope="col">By</th>
              <th scope="col">Result</th><th scope="col">New</th><th scope="col">Unmapped</th>
              <th scope="col">Error</th>
            </tr></thead>
            <tbody>
              {rows.map((l) => (
                <tr key={l.id}>
                  <td>{when(l.started_at)}</td>
                  <td>{l.sync_type_display}</td>
                  <td>{l.triggered_by_name || l.trigger_display || '—'}</td>
                  <td><Badge value={l.status} label={l.status_display} /></td>
                  <td>{l.records_created}</td>
                  <td>{l.records_unmapped}</td>
                  <td className="bd-error">{l.error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

// ---------------------------------------------------------------------------
const AttendanceSettings = () => {
  // `role` is the backend role. `user.role` is NOT: `user` is the UI-shaped
  // object built by buildUiUser, and reading the role from it left a real
  // administrator with a read-only page (found by the browser drive).
  const { role } = useAuth();
  const isAdmin = role === 'admin';
  const canMap = isAdmin || role === 'approver';
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(null);     // null | {} (new) | device
  const [panel, setPanel] = useState(null);         // {kind, device}
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [lastTest, setLastTest] = useState(null);
  // "Rate this feature" -- asked once, only after the feature has actually
  // worked for them (a device answered or a sync ran), never on arrival.
  const [succeeded, setSucceeded] = useState(false);

  const load = useCallback(() => api.dashboard()
    .then(({ data: d }) => setData(d))
    .catch(() => { setData({ devices: [], totals: {} }); setError('Devices could not be loaded.'); }),
  []);
  useEffect(() => { load(); }, [load]);

  const replace = (device) => {
    setData((d) => ({ ...d, devices: d.devices.map((x) => (x.id === device.id ? device : x)) }));
    setPanel((p) => (p && p.device.id === device.id ? { ...p, device } : p));
  };

  const run = async (device, kind) => {
    setBusyId(device.id); setError(null); setNotice(null); setLastTest(null);
    try {
      const { data: r } = kind === 'test' ? await api.test(device.id) : await api.sync(device.id);
      if (r.device) replace(r.device);
      if (kind === 'test') setLastTest({ id: device.id, result: r });
      else setNotice(`${device.name}: ${r.message}`);
      if (r.ok || (kind === 'sync' && r.status !== 'failed')) setSucceeded(true);
    } catch (err) {
      const r = err?.response?.data;
      if (r?.device) replace(r.device);
      setError(`${device.name}: ${r?.message || errorText(err, 'The request failed.')}`);
    } finally { setBusyId(null); load(); }
  };

  const remove = async (device) => {
    if (!window.confirm(`Remove ${device.name}? A device that has imported attendance is deactivated instead.`)) return;
    try {
      const response = await api.remove(device.id);
      setNotice(response.data?.detail || `${device.name} removed.`);
      load();
    } catch (err) { setError(errorText(err, 'The device could not be removed.')); }
  };

  // Computed from the rows, not read from the server's `totals`: a sync or
  // test replaces its row at once, and tiles fed by a second request showed
  // "0 online" beside a row reading "Device Online" until it landed.
  const active = (data?.devices || []).filter((d) => d.is_active);
  const sum = (rows, field) => rows.reduce((n, d) => n + (d[field] || 0), 0);
  const tiles = [
    ['Devices', active.length],
    ['Online', active.filter((d) => d.connection_status === 'online').length],
    ['Offline', active.filter((d) => d.connection_status === 'offline').length],
    ['Punches imported', sum(data?.devices || [], 'attendance_imported')],
    ['Unmatched users', sum(active, 'unmapped_users')],
  ];

  return (
    <div className="page">
      <PageHeader title="Attendance & biometric devices"
        description="How attendance is taken, and the fingerprint devices that feed it." />

      <ModeCard canEdit={isAdmin} />

      {error && <div className="br-alert br-alert-bad" role="alert">{error}</div>}
      {notice && <div className="br-alert br-alert-ok" role="status">{notice}</div>}
      {succeeded && (
        <RateThis feature="biometric-devices" question="How was connecting your device?" />
      )}

      {editing && (
        <DeviceForm key={editing.id || 'new'} initial={editing}
          onCancel={() => setEditing(null)}
          onSaved={(device, wasEdit) => {
            setEditing(null);
            setNotice(wasEdit ? `${device.name} saved.`
              : `${device.name} added. Press Sync now to read its users and attendance.`);
            load();
          }} />
      )}

      <section className="br-card" aria-labelledby="bd-dash-h">
        <div className="bd-head">
          <h2 className="br-h" id="bd-dash-h"><Fingerprint size={18} aria-hidden="true" /> Biometric devices</h2>
          {isAdmin && !editing && (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing({})}>
              <Plus size={14} aria-hidden="true" /> Add device
            </button>
          )}
        </div>
        <div className="bd-tiles">
          {tiles.map(([label, value]) => (
            <div key={label} className="bd-tile"><span className="bd-tile-v">{value}</span>
              <span className="bd-tile-l">{label}</span></div>
          ))}
        </div>

        {data === null ? <Skeleton rows={3} /> : data.devices.length === 0 ? (
          <EmptyState title="You haven't connected a biometric device yet."
            body="Add your fingerprint device by its IP address and port (ZKTeco devices use 4370). Attendance from the app keeps working without one."
            {...(isAdmin && !editing
              ? { onAction: () => setEditing({}), actionLabel: 'Add first device' } : {})} />
        ) : (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">Biometric devices</caption>
              <thead><tr>
                <th scope="col">Device</th><th scope="col">Address</th><th scope="col">Status</th>
                <th scope="col">Last sync</th><th scope="col">Imported</th>
                <th scope="col">Unmatched</th><th scope="col">Error</th><th scope="col">Actions</th>
              </tr></thead>
              <tbody>
                {data.devices.map((d) => (
                  <React.Fragment key={d.id}>
                    <tr className={d.is_active ? '' : 'bd-inactive'}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{d.name}</div>
                        <div className="bd-meta">{d.device_type_display}{d.location ? ` · ${d.location}` : ''}</div>
                        {!d.is_active && <Badge value="unknown" label="Inactive" />}
                      </td>
                      <td>
                        {d.sync_mode === 'pull' ? `${d.host}:${d.port}` : 'Pushes to platform'}
                        <div className="bd-meta">{d.sync_interval_display}</div>
                      </td>
                      <td><Badge value={d.connection_status} label={d.connection_status === 'online'
                        ? 'Device Online' : d.connection_status === 'offline' ? 'Device Offline' : 'Not tested'} /></td>
                      <td>
                        {when(d.last_sync_attempt_at || d.last_sync_at)}
                        {d.last_sync_status && <div><Badge value={d.last_sync_status} /></div>}
                      </td>
                      <td>{d.attendance_imported ?? 0}
                        {d.last_sync_imported ? <div className="bd-meta">+{d.last_sync_imported} last sync</div> : null}
                      </td>
                      <td>{d.unmapped_users ?? 0}</td>
                      <td className="bd-error">{d.last_sync_error || '—'}</td>
                      <td>
                        <div className="bd-actions">
                          {isAdmin && d.sync_mode === 'pull' && (<>
                            <button type="button" className="btn btn-ghost btn-xs" disabled={busyId === d.id}
                              onClick={() => run(d, 'test')}><PlugZap size={13} aria-hidden="true" /> Test</button>
                            <button type="button" className="btn btn-ghost btn-xs" disabled={busyId === d.id || !d.is_active}
                              onClick={() => run(d, 'sync')}>
                              <RefreshCw size={13} aria-hidden="true" className={busyId === d.id ? 'spin' : ''} /> Sync now
                            </button>
                          </>)}
                          <button type="button" className="btn btn-ghost btn-xs"
                            onClick={() => setPanel({ kind: 'users', device: d })}>
                            <Users size={13} aria-hidden="true" /> Users</button>
                          <button type="button" className="btn btn-ghost btn-xs"
                            onClick={() => setPanel({ kind: 'logs', device: d })}>
                            <ScrollText size={13} aria-hidden="true" /> Logs</button>
                          {isAdmin && (<>
                            <button type="button" className="btn btn-ghost btn-xs" aria-label={`Edit ${d.name}`}
                              onClick={() => setEditing(d)}><Pencil size={13} aria-hidden="true" /></button>
                            <button type="button" className="btn btn-ghost btn-xs" aria-label={`Remove ${d.name}`}
                              onClick={() => remove(d)}><Trash2 size={13} aria-hidden="true" /></button>
                          </>)}
                        </div>
                      </td>
                    </tr>
                    {lastTest?.id === d.id && (
                      <tr><td colSpan={8}><TestResult result={lastTest.result} /></td></tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {panel?.kind === 'users' && (
        <UsersPanel device={panel.device} canMap={canMap} onChanged={load} />
      )}
      {panel?.kind === 'logs' && <LogsPanel device={panel.device} />}
    </div>
  );
};

export default AttendanceSettings;
