import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../../components/common/PageHeader';
import Skeleton from '../../components/common/Skeleton';
import { brandingService } from '../../services/brandingService';
import { useBranding } from '../../hooks/useBranding';
import { contrastRatio, whiteTextIsReadable } from '../../utils/brandTheme';

/**
 * Phase S9 Part 1: Settings → Branding. The customer's branding centre.
 *
 * WHAT CHANGED ABOUT WHO OWNS BRANDING. Until now a tenant's logo and
 * colours could only be set by a platform operator, through the console --
 * which means every logo change was a support ticket, and the customer who
 * wanted their own name on their own workspace had to ask us to type it.
 * This page is the same fields, owned by the person whose brand it is.
 *
 * THE PREVIEW IS THE PAGE ITSELF. There is no mock browser frame: saving
 * re-themes the application live, because the colours are custom properties
 * on the document and the provider invalidates its cache on save. A
 * simulated preview would be a second rendering of the theme to keep in
 * sync with the real one, and the one that drifts is always the mock.
 *
 * CONTRAST IS REPORTED, NOT ENFORCED. A customer who picks a pale colour
 * gets unreadable buttons, and they are told so in the ratio beside the
 * field. Silently darkening their colour would mean the swatch on screen is
 * not the hex in the box, and they would spend the afternoon re-entering it.
 */
const COLOURS = [
  { field: 'color_primary', label: 'Primary colour',
    hint: 'Buttons, links, the sidebar and every highlight in the product.' },
  { field: 'color_secondary', label: 'Secondary colour',
    hint: 'Accents that sit beside the primary colour.' },
  { field: 'color_accent', label: 'Accent colour',
    hint: 'Occasional emphasis. Optional.' },
];

const TEXTS = [
  { field: 'display_name', label: 'Organisation name',
    hint: 'Shown in the header, on your login page and on your documents.' },
  { field: 'login_tagline', label: 'Login page tagline',
    hint: 'One line under your name on the sign-in screen.' },
  { field: 'dashboard_welcome', label: 'Dashboard welcome',
    hint: 'Greets your team on the home page.' },
  { field: 'report_footer_text', label: 'Document footer',
    hint: 'Printed at the foot of PDFs your team generates.' },
];

const ASSETS = [
  { field: 'logo_primary', label: 'Main logo',
    hint: 'Used in the application header and anywhere else no specific logo is set.' },
  { field: 'logo_login', label: 'Login logo',
    hint: 'Shown on your sign-in page, usually larger.' },
  { field: 'logo_email', label: 'Email logo',
    hint: 'Appears at the top of every notification email we send your team.' },
  { field: 'logo_letterhead', label: 'Letterhead logo',
    hint: 'Printed on memos, minutes and circulars. Often a wide lockup.' },
  { field: 'favicon', label: 'Browser tab icon',
    hint: 'A small square image. Shown in the browser tab and in bookmarks.' },
];

const Branding = () => {
  const { refresh } = useBranding();
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => brandingService.get()
    .then((response) => { setData(response.data); setDraft({}); })
    .catch(() => setError('Your branding could not be loaded. Try again.')), []);

  useEffect(() => { load(); }, [load]);

  const value = (field) => (draft[field] !== undefined
    ? draft[field] : (data?.[field] ?? ''));

  const set = (field) => (event) => {
    setDraft((previous) => ({ ...previous, [field]: event.target.value }));
    setNotice(null);
  };

  const save = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await brandingService.update(draft);
      setData(response.data);
      setDraft({});
      // Repaint the rest of the application now rather than on next sign-in.
      refresh();
      setNotice('Saved. Your workspace is using it already.');
    } catch (err) {
      setError(err?.response?.data?.detail
        || 'That could not be saved. Check the colours are six-digit hex values.');
    } finally { setBusy(false); }
  };

  const upload = (field) => async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await brandingService.uploadAsset(field, file);
      setData(response.data);
      refresh();
      setNotice('Uploaded.');
    } catch (err) {
      setError(err?.response?.data?.detail
        || 'That image could not be uploaded.');
    } finally { setBusy(false); event.target.value = ''; }
  };

  if (!data) {
    return (
      <div className="page">
        <PageHeader title="Branding" description="Your logo, colours and wording." />
        <Skeleton rows={6} />
      </div>
    );
  }

  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="page">
      <PageHeader
        title="Branding"
        description="Your logo, colours and wording — across the whole workspace."
      />

      {error && <div className="br-alert br-alert-bad" role="alert">{error}</div>}
      {notice && <div className="br-alert br-alert-ok" role="status">{notice}</div>}

      <section className="br-card">
        <h2 className="br-h">Colours</h2>
        <p className="br-lede">
          These are applied everywhere: the sidebar, buttons, borders and
          charts. Leave a field blank to keep the standard colour.
        </p>
        {COLOURS.map(({ field, label, hint }) => {
          const current = value(field);
          const ratio = current ? contrastRatio(current, '#FFFFFF') : null;
          const readable = current ? whiteTextIsReadable(current) : true;
          return (
            <div className="br-row" key={field}>
              <label className="br-label" htmlFor={`br-${field}`}>{label}</label>
              <div className="br-field">
                <span
                  className="br-swatch"
                  style={{ background: current || 'transparent' }}
                  aria-hidden="true"
                />
                <input
                  id={`br-${field}`}
                  className="br-input"
                  type="text"
                  placeholder="#1D4ED8"
                  value={current}
                  onChange={set(field)}
                  maxLength={7}
                  aria-describedby={`br-${field}-hint`}
                />
                {/* A native picker beside the text box: most people have a
                    hex from a brand guide, some do not have one at all. */}
                <input
                  className="br-picker"
                  type="color"
                  value={/^#[0-9a-f]{6}$/i.test(current) ? current : '#274095'}
                  onChange={set(field)}
                  aria-label={`${label} — pick visually`}
                />
              </div>
              <p className="br-hint" id={`br-${field}-hint`}>
                {hint}
                {ratio !== null && (
                  <span className={readable ? 'br-ok' : 'br-warn'}>
                    {' '}White text on this colour: {ratio.toFixed(1)}:1
                    {readable ? ' — readable.'
                      : ' — too low. Buttons and the sidebar will be hard to read.'}
                  </span>
                )}
              </p>
            </div>
          );
        })}
      </section>

      <section className="br-card">
        <h2 className="br-h">Wording</h2>
        {TEXTS.map(({ field, label, hint }) => (
          <div className="br-row" key={field}>
            <label className="br-label" htmlFor={`br-${field}`}>{label}</label>
            <div className="br-field">
              <input
                id={`br-${field}`}
                className="br-input br-input-wide"
                type="text"
                value={value(field)}
                onChange={set(field)}
                placeholder={field === 'display_name'
                  ? data.organization?.name : ''}
                aria-describedby={`br-${field}-hint`}
              />
            </div>
            <p className="br-hint" id={`br-${field}-hint`}>{hint}</p>
          </div>
        ))}
      </section>

      <div className="br-actions">
        <button
          type="button"
          className="br-save"
          onClick={save}
          disabled={busy || !dirty}
        >
          {busy ? 'Saving…' : 'Save branding'}
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
        {!dirty && !notice && (
          <span className="br-hint">Everything here is saved.</span>
        )}
      </div>

      <section className="br-card">
        <h2 className="br-h">Images</h2>
        <p className="br-lede">
          PNG or SVG. Each one is used in a different place, and whichever
          you upload first is used everywhere until you add the others.
        </p>
        <div className="br-assets">
          {ASSETS.map(({ field, label, hint }) => (
            <div className="br-asset" key={field}>
              <div className="br-asset-preview">
                {data[field]
                  ? <img src={data[field]} alt={`${label}, current`} />
                  : <span className="br-asset-none">Not set</span>}
              </div>
              <label className="br-label" htmlFor={`br-up-${field}`}>{label}</label>
              <p className="br-hint">{hint}</p>
              <input
                id={`br-up-${field}`}
                className="br-file"
                type="file"
                accept="image/*"
                onChange={upload(field)}
                disabled={busy}
              />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};

export default Branding;
