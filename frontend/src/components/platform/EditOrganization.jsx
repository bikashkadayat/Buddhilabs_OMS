import React, { useState } from 'react';
import { platformService } from '../../services/platformService';

/**
 * Part 2: "Edit Organization".
 *
 * AN ALLOW-LIST, AND A SHORT ONE. Two fields a customer would reasonably ask
 * to change are deliberately absent:
 *
 *   slug             the tenant's hostname. Renaming it breaks every bookmark
 *                    and invalidates every signed media URL already issued.
 *   document_prefix  embedded in document numbers that have been printed,
 *                    signed and filed. A document number is a historical
 *                    record; you cannot restate one.
 *
 * Both are changeable in principle, but through a deliberate migration with a
 * redirect and a cutover — not a form. The server enforces the same list
 * (console.EDITABLE_FIELDS) and refuses anything else, so this is the shape of
 * the rule rather than the rule itself.
 */
const FIELDS = [
  ['name', 'Organization name', 'text'],
  ['email', 'Billing email', 'email'],
  ['phone', 'Phone', 'text'],
  ['industry', 'Industry', 'text'],
  ['country', 'Country (ISO 3166-1 alpha-2)', 'text'],
  ['timezone', 'Time zone', 'text'],
  ['site_url', 'Public site URL', 'url'],
  ['domain', 'Custom domain', 'text'],
];

const EditOrganization = ({ organization, onClose, onSaved }) => {
  const [form, setForm] = useState(() =>
    Object.fromEntries(FIELDS.map(([key]) => [key, organization[key] ?? ''])));
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  const set = (key) => (event) =>
    setForm((f) => ({ ...f, [key]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      await platformService.update(organization.slug, form);
      onSaved();
    } catch (error) {
      setErrors(error.response?.data
        || { detail: 'The changes could not be saved.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pf-modal" role="dialog" aria-modal="true"
         aria-label={`Edit ${organization.name}`}>
      <form className="pf-modal-card" onSubmit={submit}>
        <h3 className="pf-modal-title">Edit {organization.name}</h3>
        <p className="pf-modal-lead">
          The workspace address ({organization.slug}) and the document prefix
          ({organization.document_prefix}) are permanent and are not editable
          here — both are embedded in things already issued.
        </p>
        {errors.detail && <p className="pf-err" role="alert">{errors.detail}</p>}

        <div className="pf-field-row">
          {FIELDS.map(([key, label, type]) => (
            <label className="pf-field" key={key}>
              <span>{label}</span>
              <input type={type} value={form[key]} onChange={set(key)} />
              {errors[key] && <em className="pf-err">{errors[key]}</em>}
            </label>
          ))}
        </div>

        <div className="pf-modal-actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose}
                  disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
};

export default EditOrganization;
