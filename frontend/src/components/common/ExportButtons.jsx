import React, { useState } from 'react';
import { Download, FileText } from 'lucide-react';

import { saveBlob } from '../../services/reportService';

/**
 * Export buttons that actually download (Phase FIX).
 *
 * WHY THIS IS A COMPONENT AND NOT A LINK
 * --------------------------------------
 * These used to be plain `<a href="/api/v1/.../?export=csv">` links. This app
 * authenticates with a JWT held in JavaScript, not a session cookie, so a link
 * navigation carries no Authorization header: the endpoint answered 401 and the
 * browser either rendered that JSON as a blank-looking page or saved it as the
 * "file". Every symptom reported — blank page, no file, broken download — came
 * from that one line.
 *
 * The fix is to fetch through the authenticated client and hand the blob to
 * `saveBlob`, which is what every older module in this system already did. This
 * component exists so the rule lives in ONE place: five pages had their own
 * copy of the broken pattern, and five copies of the fix would rot the same way.
 *
 * WHAT IT REPORTS WHEN IT FAILS
 * -----------------------------
 * A failed export says so, in place, with the server's own message where there
 * is one. The previous behaviour — navigate away and show raw JSON — is the
 * reason this was reported as "no response" rather than as a permission error,
 * which is what it usually was.
 */
const FORMATS = {
  csv: { label: 'CSV', icon: Download, ext: 'csv' },
  pdf: { label: 'PDF', icon: FileText, ext: 'pdf' },
  excel: { label: 'Excel', icon: Download, ext: 'xlsx' },
};

/**
 * Read a Blob as text.
 *
 * `Blob.prototype.text()` is not universally available — Safari before 14 and
 * older Edge lack it, and so does jsdom, which is how this was caught. Falling
 * back to FileReader means the error message survives on the browsers where an
 * error is most likely to be the thing somebody needs to read.
 */
const blobText = (blob) => (
  typeof blob.text === 'function'
    ? blob.text()
    : new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    })
);

/**
 * The server's own message, where there is one.
 *
 * With `responseType: 'blob'` the ERROR body is a Blob too, so reading
 * `err.response.data.detail` gives undefined and the user is told nothing —
 * which is how a permission refusal becomes "nothing happened".
 */
const messageFor = async (err) => {
  const fallback = 'That export could not be downloaded.';
  const body = err?.response?.data;
  try {
    if (body instanceof Blob) {
      return JSON.parse(await blobText(body))?.detail || fallback;
    }
    return body?.detail || fallback;
  } catch {
    return fallback;   // not JSON — an HTML error page, or a network failure
  }
};

const ExportButtons = ({
  download, name = 'report', formats = ['csv', 'pdf'], size = 'lr-btn',
  labelPrefix = '',
}) => {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');

  const run = async (format) => {
    setBusy(format);
    setError('');
    try {
      const response = await download(format);
      // `saveBlob` prefers the server's Content-Disposition filename; the
      // fallback here is only used when the server did not set one.
      saveBlob(response, `${name}.${FORMATS[format].ext}`);
    } catch (err) {
      setError(await messageFor(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      {formats.map((format) => {
        const spec = FORMATS[format];
        const Icon = spec.icon;
        return (
          <button key={format} type="button" className={size}
            disabled={busy !== null}
            onClick={() => run(format)}>
            <Icon size={14} aria-hidden="true" />
            {' '}
            {busy === format ? 'Preparing…' : `${labelPrefix}${spec.label}`}
          </button>
        );
      })}
      {error && (
        <span className="task-callout is-no" role="alert">{error}</span>
      )}
    </>
  );
};

export default ExportButtons;
