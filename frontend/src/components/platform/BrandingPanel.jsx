import React, { useEffect, useState } from 'react';
import { platformService } from '../../services/platformService';

/**
 * Part 7: per-tenant branding.
 *
 * WHAT A TENANT SEES OF THIS, AND WHAT IT MUST NOT SEE. Only five of these
 * fields reach an unauthenticated visitor — name, login logo, two colours and
 * the tagline — through the pre-login branding endpoint, which answers for the
 * host it was asked on. Everything else is behind authentication, and no
 * tenant can read another's at all.
 *
 * The three HTML fields are tenant-authored markup stored as written and
 * sanitised at every render site. Sanitising on the way in would silently
 * discard markup the author can see they typed.
 */
const TEXT_FIELDS = [
  ['display_name', 'Display name', 'Overrides the organization name in the product.'],
  ['login_tagline', 'Login tagline', 'One line under the logo on the sign-in page.'],
  ['dashboard_welcome', 'Dashboard welcome', ''],
  ['report_footer_text', 'Report footer', 'Printed at the foot of generated reports.'],
];

const COLOUR_FIELDS = [
  ['color_primary', 'Primary colour'],
  ['color_secondary', 'Secondary colour'],
];

// `logo` and `favicon` are stored on the Organization rather than on the
// branding row — they were modelled in Phase S1 as identity, before branding
// existed as its own record. The console treats all six the same; the server
// routes each to the right table.
const ASSET_FIELDS = [
  ['logo_login', 'Sign-in logo'],
  ['logo_primary', 'Product logo'],
  ['logo_email', 'Email logo'],
  ['logo_letterhead', 'Letterhead logo'],
  ['logo', 'Organization logo'],
  ['favicon', 'Favicon'],
];

const BrandingPanel = ({ slug }) => {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    platformService.branding(slug)
      .then((response) => setForm(response.data))
      .catch(() => setStatus('Branding could not be loaded.'));
  }, [slug]);

  const set = (key) => (event) =>
    setForm((f) => ({ ...f, [key]: event.target.value }));

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setStatus(null);
    setErrors({});
    const body = Object.fromEntries(
      [...TEXT_FIELDS, ...COLOUR_FIELDS].map(([key]) => [key, form[key] ?? '']));
    try {
      const response = await platformService.setBranding(slug, body);
      setForm(response.data);
      setStatus('Branding saved.');
    } catch (error) {
      setErrors(error.response?.data || {});
      setStatus('Branding was not saved.');
    } finally {
      setSaving(false);
    }
  };

  const upload = (field) => async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setStatus(null);
    try {
      const response = await platformService.setBrandingAsset(slug, field, file);
      setForm(response.data);
      setStatus(`${field.replace(/_/g, ' ')} uploaded.`);
    } catch {
      setStatus(`${field.replace(/_/g, ' ')} could not be uploaded.`);
    }
  };

  if (!form) {
    return (
      <section className="pf-panel" aria-label="Branding">
        <h2 className="pf-panel-title">Branding</h2>
        <p className="pf-note">{status || 'Loading…'}</p>
      </section>
    );
  }

  return (
    <section className="pf-panel" aria-label="Branding">
      <h2 className="pf-panel-title">Branding</h2>
      <p className="pf-modal-note">
        Blank means “inherit the platform default”, so a customer who
        customises nothing is indistinguishable from the product as it ships.
      </p>
      {status && <p className="pf-note" role="status">{status}</p>}

      <form onSubmit={save}>
        {TEXT_FIELDS.map(([key, label, hint]) => (
          <label className="pf-field" key={key}>
            <span>{label}</span>
            <input value={form[key] ?? ''} onChange={set(key)} />
            {hint && <small>{hint}</small>}
            {errors[key] && <em className="pf-err">{errors[key]}</em>}
          </label>
        ))}

        <div className="pf-field-row">
          {COLOUR_FIELDS.map(([key, label]) => (
            <label className="pf-field" key={key}>
              <span>{label}</span>
              <span className="pf-colour">
                <input
                  type="color"
                  value={/^#[0-9a-fA-F]{6}$/.test(form[key] || '') ? form[key] : '#274095'}
                  onChange={set(key)}
                  aria-label={`${label} picker`}
                />
                <input value={form[key] ?? ''} onChange={set(key)}
                       placeholder="#274095" />
              </span>
              {errors[key] && <em className="pf-err">{errors[key]}</em>}
            </label>
          ))}
        </div>

        <div className="pf-modal-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save branding'}
          </button>
        </div>
      </form>

      <h3 className="pf-modal-sub">Logos</h3>
      <div className="pf-assets">
        {ASSET_FIELDS.map(([key, label]) => (
          <label className="pf-field" key={key}>
            <span>{label}</span>
            <input type="file" accept="image/*" onChange={upload(key)} />
            {form[key] && <small>Uploaded.</small>}
          </label>
        ))}
      </div>
    </section>
  );
};

export default BrandingPanel;
