/**
 * The words an operator hands a new customer.
 *
 * ONE PLACE, so "Copy credentials", "Copy welcome message" and "Copy full
 * package" cannot disagree about the URL or the address — they are built
 * from the same `access` package the server returns, which is also what the
 * welcome email is rendered from.
 *
 * THE PASSWORD IS OPTIONAL. It exists in the browser only inside the
 * creation dialog. Everywhere else the text says how the customer will get
 * it instead of printing a placeholder that looks like a password.
 */

const NO_PASSWORD = '(sent to you separately)';

const trialLine = (access) => {
  const trial = access?.trial;
  if (!trial?.ends_on) return null;
  const ends = new Date(`${trial.ends_on}T00:00:00`).toLocaleDateString(
    undefined, { day: 'numeric', month: 'long', year: 'numeric' });
  return trial.days
    ? `Your ${trial.days}-day trial runs until ${ends}.`
    : `Your trial runs until ${ends}.`;
};

export const loginUrlText = (access) => access?.login_url || '';

export const credentialsText = (access, password) => [
  `Sign in at: ${access.login_url}`,
  `Email: ${access.administrator?.email || ''}`,
  `Temporary password: ${password || NO_PASSWORD}`,
].join('\n');

export const welcomeMessage = (access, password) => {
  const name = access.organization?.name || 'your organization';
  const lines = [
    `Hello ${name} team,`,
    '',
    `Your ${name} workspace is ready.`,
    '',
    credentialsText(access, password),
    '',
    'You will be asked to choose your own password the first time you sign in.',
  ];
  const trial = trialLine(access);
  if (trial) lines.push(trial);
  if (access.support_email) {
    lines.push('', `Trouble signing in? Write to ${access.support_email}.`);
  }
  lines.push('', access.powered_by || 'Powered by Buddhi Labs');
  return lines.join('\n');
};

/** Everything, for an operator's own notes or a ticket. */
export const fullPackage = (access, password) => {
  const lines = [
    `Organization: ${access.organization?.name || ''}`,
    `Login URL: ${access.login_url}`,
  ];
  if (access.custom_domain) {
    lines.push(`Custom domain: ${access.custom_domain.hostname} — ${
      access.custom_domain.serving ? 'live' : access.custom_domain.status_display}`);
  }
  if (access.administrator) {
    lines.push(`Administrator: ${access.administrator.name} <${access.administrator.email}>`);
  }
  lines.push(`Temporary password: ${password || NO_PASSWORD}`);
  if (access.plan) lines.push(`Plan: ${access.plan.name}`);
  const trial = trialLine(access);
  if (trial) lines.push(`Trial: ${trial.replace(/^Your /, '')}`);
  lines.push('', '— Welcome message —', '', welcomeMessage(access, password));
  return lines.join('\n');
};

/**
 * Copy text, including where the Clipboard API is not available.
 *
 * `navigator.clipboard` exists only in a secure context. A console reached
 * over plain HTTP on an internal address — which is how a fresh deployment
 * is usually first opened — has none, and a copy button that silently does
 * nothing is worse than no button. The textarea fallback works everywhere.
 */
export const copyText = async (text) => {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext !== false) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the fallback */ }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand?.('copy');
    document.body.removeChild(area);
    return Boolean(ok);
  } catch {
    return false;
  }
};
