import React, { useEffect, useState, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ArrowLeft, User, Mail, Shield, Calendar, Camera, Trash2, Pencil, Save, X, Phone, MapPin, Lock } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { profileService } from '../services/profileService';
import { roleLabel, employmentTypeLabel } from '../services/roles';
import { todayBS } from '../services/bsDate';
import Avatar from '../components/common/Avatar';
import MyBiometricAttendance from '../components/attendance/MyBiometricAttendance';
import { ChangePassword, Sessions } from '../components/account/AccountSecurity';

const EDITABLE = ['first_name', 'last_name', 'phone', 'address',
  'emergency_contact_name', 'emergency_contact_number', 'date_of_birth', 'gender', 'bio'];

const bsAd = (iso) => {
  if (!iso) return '—';
  try {
    const ad = new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    return `${todayBS(new Date(iso))} BS · ${ad}`;
  } catch { return iso; }
};

const extractErr = (err) => {
  const d = err?.response?.data;
  if (!d) return err?.message || 'Something went wrong.';
  if (typeof d === 'string') return d;
  if (d.detail) return d.detail;
  const parts = Object.entries(d).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(' ') : v}`);
  return parts.join(' \n') || 'Save failed.';
};

const Card = ({ icon: Icon, color, title, managed, children }) => (
  <div style={{ padding: 20, background: 'var(--bg-main)', borderRadius: 12 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
      <div style={{ width: 36, height: 36, borderRadius: 8, background: color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Icon size={18} color="white" />
      </div>
      <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{title}</span>
      {managed && (
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-label)', color: 'var(--text-muted)' }}>
          <Lock size={12} /> Managed by HR
        </span>
      )}
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>{children}</div>
  </div>
);

const RO = ({ label, value }) => (
  <div>
    <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginBottom: 2 }}>{label}</div>
    <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-primary)', fontWeight: 500 }}>{value || '—'}</div>
  </div>
);

const Profile = () => {
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth();
  const fileRef = useRef(null);

  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState({});
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const load = async () => {
    try {
      const data = await profileService.me();
      setProfile(data);
      setForm(Object.fromEntries(EDITABLE.map((k) => [k, data[k] ?? ''])));
    } catch (e) {
      setError(extractErr(e));
    }
  };
  useEffect(() => { load(); }, []);
  // The account menu links to #security and #sessions; the router changes
  // the URL and nothing else, so scroll there once the page has rendered.
  const location = useLocation();
  useEffect(() => {
    if (!location.hash || !profile) return;
    document.getElementById(location.hash.slice(1))
      ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }, [location.hash, profile]);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const startEdit = () => { setSuccess(''); setError(''); setEditing(true); };
  const cancel = () => {
    setForm(Object.fromEntries(EDITABLE.map((k) => [k, profile[k] ?? ''])));
    setEditing(false); setError('');
  };

  const save = async () => {
    setSaving(true); setError(''); setSuccess('');
    try {
      const payload = { ...form, date_of_birth: form.date_of_birth || null };
      const updated = await profileService.update(payload);
      setProfile(updated);
      setForm(Object.fromEntries(EDITABLE.map((k) => [k, updated[k] ?? ''])));
      setEditing(false);
      setSuccess('Profile updated successfully.');
      await refreshUser();
    } catch (e) {
      setError(extractErr(e));
    } finally {
      setSaving(false);
    }
  };

  const onPhotoPick = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPhotoBusy(true); setError(''); setSuccess('');
    try {
      const updated = await profileService.uploadPhoto(file);
      setProfile(updated);
      await refreshUser();
      setSuccess('Photo updated.');
    } catch (err) {
      setError(extractErr(err));
    } finally {
      setPhotoBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const onPhotoRemove = async () => {
    setPhotoBusy(true); setError('');
    try {
      const updated = await profileService.removePhoto();
      setProfile(updated);
      await refreshUser();
    } catch (err) {
      setError(extractErr(err));
    } finally {
      setPhotoBusy(false);
    }
  };

  if (!profile) {
    return <div className="page"><div style={{ padding: 40, color: 'var(--text-muted)' }}>{error || 'Loading profile…'}</div></div>;
  }

  const fullName = `${profile.first_name || ''} ${profile.last_name || ''}`.trim() || profile.username;
  const initials = (fullName || 'U').split(' ').map((w) => w[0] || '').join('').toUpperCase().slice(0, 2);

  // A render FUNCTION (not a nested component) so inputs keep focus while typing.
  const editableField = ({ label, k, type = 'text', placeholder, textarea }) => (
    <div key={k}>
      <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      {editing ? (
        textarea
          ? <textarea className="lr-input" rows={3} value={form[k] ?? ''} onChange={set(k)} placeholder={placeholder}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 'var(--fs-body)', resize: 'vertical' }} />
          : type === 'gender'
            ? <select value={form.gender ?? ''} onChange={set('gender')}
                style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 'var(--fs-body)' }}>
                <option value="undisclosed">Prefer not to say</option>
                <option value="female">Female</option>
                <option value="male">Male</option>
              </select>
            : <input type={type} value={form[k] ?? ''} onChange={set(k)} placeholder={placeholder}
                style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 'var(--fs-body)' }} />
      ) : (
        <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-primary)', fontWeight: 500 }}>
          {k === 'date_of_birth' ? bsAd(profile.date_of_birth)
            : k === 'gender' ? (profile.gender ? profile.gender.charAt(0).toUpperCase() + profile.gender.slice(1) : '—')
            : (profile[k] || '—')}
        </div>
      )}
    </div>
  );

  return (
    <div className="page" style={{ paddingBottom: 60 }}>
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate(-1)}><ArrowLeft size={18} /></button>
            User Profile
          </div>
          <div className="pg-title">My Profile</div>
          <div className="pg-desc">View and manage your account information</div>
        </div>
        <div className="pg-head-right" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {editing ? (
            <>
              <button className="btn btn-ghost" onClick={cancel} disabled={saving}><X size={16} /> Cancel</button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                <Save size={16} /> {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          ) : (
            <button className="btn btn-primary" onClick={startEdit}><Pencil size={16} /> Edit Profile</button>
          )}
        </div>
      </div>

      {(error || success) && (
        <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, fontSize: 'var(--fs-sm)', whiteSpace: 'pre-line',
          background: error ? 'rgba(220,38,38,.08)' : 'rgba(16,185,129,.10)', color: error ? '#b91c1c' : '#047857' }}>
          {error || success}
        </div>
      )}

      <div className="table-card" style={{ padding: 32 }}>
        {/* Hero: photo + name + role */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginBottom: 32, flexWrap: 'wrap' }}>
          <div style={{ position: 'relative', width: 96, height: 96 }}>
            <Avatar photo={profile.profile_photo} initials={initials} color={user?.color || '#6B7280'} size={96} fontSize={32} />
            <button type="button" onClick={() => fileRef.current?.click()} disabled={photoBusy}
              title="Change photo"
              style={{ position: 'absolute', right: -2, bottom: -2, width: 34, height: 34, borderRadius: '50%',
                background: 'var(--brand-blue, #2563EB)', color: '#fff', border: '2px solid var(--white, #fff)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
              <Camera size={16} />
            </button>
            <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={onPhotoPick} style={{ display: 'none' }} />
          </div>
          <div>
            <h2 style={{ fontSize: 'var(--fs-display)', fontWeight: 700, color: 'var(--text-primary)', marginBottom: 6 }}>{fullName}</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ background: user?.color || '#6B7280', color: '#fff', padding: '4px 12px', borderRadius: 20, fontSize: 'var(--fs-meta)', fontWeight: 600 }}>
                {roleLabel(profile.role)}
              </span>
              {profile.leave_category && (
                <span style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-secondary)' }}>{profile.leave_category_display || profile.leave_category}</span>
              )}
              {profile.profile_photo && (
                <button type="button" className="btn btn-ghost" onClick={onPhotoRemove} disabled={photoBusy}
                  style={{ fontSize: 'var(--fs-meta)', padding: '4px 10px' }}><Trash2 size={13} /> Remove photo</button>
              )}
              {photoBusy && <span style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>Working…</span>}
            </div>
            <div style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)', marginTop: 6 }}>JPG, PNG or WEBP · up to 2 MB · squared automatically</div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 24 }}>
          {/* Personal (editable) */}
          <Card icon={User} color="var(--brand-blue, #2563EB)" title="Personal Information">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {editableField({ label: "First name", k: "first_name", placeholder: "First name" })}
              {editableField({ label: "Last name", k: "last_name", placeholder: "Last name" })}
            </div>
            {editableField({ label: "Date of birth", k: "date_of_birth", type: "date" })}
            {editableField({ label: "Gender", k: "gender", type: "gender" })}
            {editableField({ label: "Bio / about", k: "bio", textarea: true, placeholder: "A short description about you" })}
          </Card>

          {/* Contact (editable, except email) */}
          <Card icon={Phone} color="var(--success, #10B981)" title="Contact Details">
            <RO label="Email (login)" value={profile.email} />
            {editableField({ label: "Phone", k: "phone", placeholder: "+977 …" })}
            {editableField({ label: "Address", k: "address", placeholder: "Street, city" })}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {editableField({ label: "Emergency contact", k: "emergency_contact_name", placeholder: "Name" })}
              {editableField({ label: "Emergency number", k: "emergency_contact_number", placeholder: "Phone" })}
            </div>
          </Card>

          {/* Role & permissions (read-only) */}
          <Card icon={Shield} color="var(--warning, #F59E0B)" title="Role & Permissions" managed>
            <RO label="Role" value={roleLabel(profile.role)} />
            <RO label="Department" value={profile.department_name} />
            <RO label="Designation" value={profile.designation} />
            <RO label="Manager" value={profile.manager
              ? `${profile.manager.name}${profile.manager.designation ? ` · ${profile.manager.designation}` : ''}`
              : null} />
            <RO label="Employment type" value={employmentTypeLabel(profile.employment_type)} />
            <RO label="Leave category" value={profile.leave_category_display || profile.leave_category} />
          </Card>

          {/* Account (read-only) */}
          <Card icon={Calendar} color="#6366F1" title="Account Info" managed>
            <RO label="Employee ID" value={profile.employee_id} />
            <RO label="Username" value={profile.username} />
            <RO label="Account status" value={profile.is_active ? 'Active' : 'Inactive'} />
            <RO label="Date of joining" value={bsAd(profile.date_of_joining)} />
          </Card>
        </div>
      </div>

      <div className="acc-grid">
        <ChangePassword />
        <Sessions />
      </div>

      <MyBiometricAttendance />
    </div>
  );
};

export default Profile;
