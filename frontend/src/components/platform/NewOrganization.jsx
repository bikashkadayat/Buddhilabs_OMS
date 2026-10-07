import React, { useEffect, useState } from 'react';
import { PLATFORM_NAME } from '../../config/platform';
import { Link } from 'react-router-dom';
import { CheckCircle2 } from 'lucide-react';
import { platformService } from '../../services/platformService';
import HandoverCard from './HandoverCard';

/**
 * Part 3: "Tenant creation is one-click."
 *
 * ONE REQUEST, and the server does the whole job: organization, settings,
 * branding, subscription, bootstrap configuration (departments, leave types,
 * shifts, an attendance policy, minute types, task templates, competencies)
 * and optionally the first administrator. There is no second step, no "now
 * seed the data" button, and no shell command — which is the requirement, and
 * it is also what stops a half-built tenant existing at all.
 *
 * THE RECEIPT MATTERS AS MUCH AS THE CREATE. After provisioning this shows
 * what was created and whether anything is still missing, because the failure
 * this phase exists to fix was invisible: a tenant that provisioned
 * "successfully" and could not approve a day's leave.
 *
 * The administrator's temporary password is shown ONCE. It is never stored and
 * never logged, so this dialog is the only moment it can be handed over; after
 * that the operator resets it instead.
 */
const slugify = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 63);
const prefixFrom = (value) => value.replace(/[^A-Za-z0-9]+/g, '').toUpperCase().slice(0, 12);

// Shown in the subdomain preview. The server is authoritative; this is
// the label an operator reads while typing, so it comes from the build
// rather than a round trip per keystroke.
const BASE_DOMAIN = import.meta.env.VITE_TENANT_BASE_DOMAIN
  || 'buddhilabs.com';

const NewOrganization = ({ onClose, onCreated }) => {
  const [plans, setPlans] = useState([]);
  const [form, setForm] = useState({
    name: '', slug: '', document_prefix: '', email: '', industry: '',
    country: 'NP', plan_code: '', admin_email: '', admin_name: '',
    domain: '', color_primary: '', color_secondary: '',
  });
  // Files are held apart from the text fields: `set()` reads
  // `event.target.value`, which for a file input is a fake path, not a file.
  const [logo, setLogo] = useState(null);
  const [favicon, setFavicon] = useState(null);
  const [touched, setTouched] = useState({});
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  useEffect(() => {
    platformService.plans()
      .then((response) => {
        const purchasable = response.data.filter((plan) => plan.is_active);
        setPlans(purchasable);
        setForm((f) => (f.plan_code ? f : { ...f, plan_code: purchasable[0]?.code || '' }));
      })
      .catch(() => setPlans([]));
  }, []);

  const set = (key) => (event) => {
    const { value } = event.target;
    setForm((f) => {
      const next = { ...f, [key]: value };
      // The address and the document prefix are DERIVED until the operator
      // edits them. Both are permanent — the address is the tenant's hostname
      // and the prefix is embedded in every document number they ever issue —
      // so they are offered rather than imposed.
      if (key === 'name') {
        if (!touched.slug) next.slug = slugify(value);
        if (!touched.document_prefix) next.document_prefix = prefixFrom(value);
      }
      return next;
    });
    if (key === 'slug' || key === 'document_prefix') {
      setTouched((t) => ({ ...t, [key]: true }));
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      const body = Object.fromEntries(
        Object.entries(form).filter(([, value]) => value !== ''));
      if (logo) body.logo = logo;
      if (favicon) body.favicon = favicon;
      const response = await platformService.provision(body);
      setDone(response.data);
    } catch (error) {
      setErrors(error.response?.data || {
        detail: 'The organization could not be created. Check your connection and try again.',
      });
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    const created = done.provisioning?.bootstrap || {};
    const gaps = done.provisioning?.gaps || {};
    const total = Object.values(created).reduce((n, count) => n + count, 0);
    return (
      <div className="pf-modal" role="dialog" aria-modal="true"
           aria-label="Workspace ready">
        <div className="pf-modal-card pf-modal-wide">
          <div className="pf-done-head">
            <span className="pf-done-mark" aria-hidden="true">
              <CheckCircle2 size={22} />
            </span>
            <div>
              <h2 className="pf-modal-title">Workspace ready</h2>
              <p className="pf-modal-lead">
                {done.name} is open and ready to use.
                {done.access?.administrator
                  ? ' Send the details below to their administrator and they can sign in straight away.'
                  : ' Add an administrator from the organization page so somebody can sign in.'}
              </p>
            </div>
          </div>

          <HandoverCard
            access={done.access}
            password={done.admin_initial_password}
            slug={done.slug}
            title="Ready to share"
          />

          {Object.keys(gaps).length > 0 ? (
            <div className="pf-alert" role="alert">
              <strong>Setup is incomplete.</strong> Still missing:{' '}
              {Object.keys(gaps).join(', ')}. Open the organization and choose
              “Repair configuration”.
            </div>
          ) : (
            <details className="pf-done-receipt">
              <summary>
                Set up for them: {total} items across {Object.keys(created).length} areas
              </summary>
              <ul className="pf-kv">
                {Object.entries(created).map(([key, count]) => (
                  <li key={key}>
                    <span>{key.replace(/_/g, ' ')}</span><b>{count}</b>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="pf-modal-actions">
            <Link to={`/platform/organizations/${done.slug}`} className="btn btn-primary">
              Go to {done.name}
            </Link>
            <button type="button" className="btn btn-ghost" onClick={onCreated}>Done</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="pf-modal" role="dialog" aria-modal="true"
         aria-label="New organization">
      <form className="pf-modal-card" onSubmit={submit}>
        <h2 className="pf-modal-title">New organization</h2>
        <p className="pf-modal-lead">
          Fill this in once. Their workspace is built, set up and opened the
          moment you press Create, and you get everything to send them.
        </p>

        {errors.detail && <p className="pf-err" role="alert">{errors.detail}</p>}

        <label className="pf-field">
          <span>Organization name</span>
          <input required value={form.name} onChange={set('name')} />
        </label>

        <div className="pf-field-row">
          <label className="pf-field">
            <span>Workspace address</span>
            <input required value={form.slug} onChange={set('slug')}
                   pattern="[a-z0-9]+" />
            <small>
              Permanent. Their workspace opens at{' '}
              <b>{form.slug || 'address'}.{BASE_DOMAIN}</b>
            </small>
            {errors.slug && <em className="pf-err">{errors.slug}</em>}
          </label>
          <label className="pf-field">
            <span>Document prefix</span>
            <input required value={form.document_prefix}
                   onChange={set('document_prefix')} maxLength={12} />
            <small>Permanent. Appears in every document number issued.</small>
            {errors.document_prefix && (
              <em className="pf-err">{errors.document_prefix}</em>
            )}
          </label>
        </div>

        <div className="pf-field-row">
          <label className="pf-field">
            <span>Billing email</span>
            <input type="email" required value={form.email} onChange={set('email')} />
            {errors.email && <em className="pf-err">{errors.email}</em>}
          </label>
          <label className="pf-field">
            <span>Industry</span>
            <input value={form.industry} onChange={set('industry')}
                   placeholder="education" />
            <small>Seeds a sector-appropriate configuration.</small>
          </label>
        </div>

        <div className="pf-field-row">
          <label className="pf-field">
            <span>Country</span>
            <input value={form.country} onChange={set('country')}
                   maxLength={2} placeholder="NP" />
          </label>
          <label className="pf-field">
            <span>Plan</span>
            <select value={form.plan_code} onChange={set('plan_code')}>
              {plans.map((plan) => (
                <option key={plan.code} value={plan.code}>
                  {plan.name}
                  {plan.trial_days ? ` — ${plan.trial_days}-day trial` : ''}
                </option>
              ))}
            </select>
          </label>
        </div>

        <h3 className="pf-modal-sub">Branding (optional)</h3>
        <p className="pf-modal-note">
          Set here and the customer never uploads it. One logo becomes their
          header, their sign-in page, their emails and their PDF letterhead.
          Leave blank and they inherit {PLATFORM_NAME}&apos; own until they
          set theirs.
        </p>

        <div className="pf-field-row">
          <label className="pf-field">
            <span>Organization logo</span>
            <input type="file" accept="image/*"
                   onChange={(e) => setLogo(e.target.files?.[0] || null)} />
            <small>PNG or SVG. Used everywhere their brand appears.</small>
            {errors.logo && <em className="pf-err">{errors.logo}</em>}
          </label>
          <label className="pf-field">
            <span>Favicon</span>
            <input type="file" accept="image/*"
                   onChange={(e) => setFavicon(e.target.files?.[0] || null)} />
            <small>A small square image for the browser tab.</small>
            {errors.favicon && <em className="pf-err">{errors.favicon}</em>}
          </label>
        </div>

        <div className="pf-field-row">
          <label className="pf-field">
            <span>Primary colour</span>
            <span className="pf-colour">
              <input type="color" value={form.color_primary || '#023530'}
                     onChange={set('color_primary')}
                     aria-label="Primary colour — pick visually" />
              <input value={form.color_primary} onChange={set('color_primary')}
                     placeholder="#1D4ED8" maxLength={7} />
            </span>
            <small>Buttons, links, the sidebar — their whole workspace.</small>
            {errors.color_primary && (
              <em className="pf-err">{errors.color_primary}</em>
            )}
          </label>
          <label className="pf-field">
            <span>Secondary colour</span>
            <span className="pf-colour">
              <input type="color" value={form.color_secondary || '#023530'}
                     onChange={set('color_secondary')}
                     aria-label="Secondary colour — pick visually" />
              <input value={form.color_secondary}
                     onChange={set('color_secondary')}
                     placeholder="#F59E0B" maxLength={7} />
            </span>
            {errors.color_secondary && (
              <em className="pf-err">{errors.color_secondary}</em>
            )}
          </label>
        </div>

        <h3 className="pf-modal-sub">Custom domain (optional)</h3>
        <p className="pf-modal-note">
          Their workspace opens at{' '}
          <code>{form.slug || 'subdomain'}.{BASE_DOMAIN}</code> as soon as you
          submit this — nothing further is needed. A domain of their own is
          additional, and <strong>claims</strong> the name rather than
          granting it: it resolves only once DNS proves they own it, from
          Settings → Custom domain.
        </p>
        <label className="pf-field">
          <span>Their own hostname</span>
          <input value={form.domain} onChange={set('domain')}
                 placeholder="hr.theircompany.com" />
          {errors.domain && <em className="pf-err">{errors.domain}</em>}
        </label>

        <h3 className="pf-modal-sub">First administrator</h3>
        <p className="pf-modal-note">
          The person who will sign in first and invite everyone else. They get
          a temporary password, shown to you once, and choose their own when
          they first sign in. You can leave this blank and add them later.
        </p>
        <div className="pf-field-row">
          <label className="pf-field">
            <span>Administrator email</span>
            <input type="email" value={form.admin_email} onChange={set('admin_email')} />
            {errors.admin_email && <em className="pf-err">{errors.admin_email}</em>}
          </label>
          <label className="pf-field">
            <span>Administrator name</span>
            <input value={form.admin_name} onChange={set('admin_name')} />
          </label>
        </div>

        <div className="pf-modal-actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Creating workspace…' : 'Create organization'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
};

export default NewOrganization;
