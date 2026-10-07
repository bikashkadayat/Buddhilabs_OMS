/**
 * Deep links and share targets (Phase TASK-DEEP-LINK-SHARING).
 *
 * MODULE-SCOPED, NOT TASK-SCOPED
 * ------------------------------
 * Nothing in this file knows what a task is. It takes a reference, a title and
 * a path, and returns a canonical URL plus the handful of share targets an
 * office actually uses. Memos, minutes, circulars, leave, assets and appraisals
 * each have a reference and a detail route, so each can use this unchanged —
 * which is the point: a second copy of this logic is how the WhatsApp text and
 * the email body start disagreeing about what the link is.
 *
 * WHY THE SHARE TEXT REPEATS THE REFERENCE
 * ----------------------------------------
 * A pasted URL in a chat is an opaque blue line. The reference above it survives
 * being forwarded, quoted, read aloud on a phone call, and pasted into a system
 * that eats hyperlinks — which is most of them.
 */

/** The absolute, shareable URL for a record's canonical path. */
export const canonicalUrl = (path, origin) => {
  const base = origin
    || (typeof window !== 'undefined' ? window.location.origin : '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
};

/**
 * The path a record should live at: its REFERENCE, not its id.
 *
 * Falls back to the id when a record has no reference yet — a draft saved
 * before its number was issued still has to be linkable.
 */
export const canonicalPath = (base, reference, id) =>
  `/${base.replace(/^\/|\/$/g, '')}/${reference || id}`;

/** The plain-text block shared into a chat window. */
export const shareText = ({ kind = 'Task', title, reference, url }) => [
  `${kind}: ${title}`,
  '',
  'Reference:',
  reference,
  '',
  'Open:',
  url,
].join('\n');

/** Subject and body for an email client. */
export const shareEmail = ({ kind = 'Task', title, reference, url, action }) => ({
  subject: `${action || `${kind} Review Required`} – ${reference}`,
  body: [
    `${kind}:`,
    reference,
    '',
    'Title:',
    title,
    '',
    'Link:',
    url,
  ].join('\n'),
});

/**
 * Every target, ready to open. `href` for the ones a browser can follow
 * directly; the caller decides whether to open in a tab or navigate.
 *
 * WhatsApp uses wa.me, which hands off to the desktop app or the phone; Teams
 * uses its documented share-to-Teams deep link. Neither needs a key, an SDK or
 * a server round trip, and neither sends anything anywhere until the person
 * clicks — a share sheet that pre-registered the link somewhere would be a
 * silent leak of an internal reference.
 */
export const shareTargets = (record) => {
  const { subject, body } = shareEmail(record);
  const text = shareText(record);
  return {
    email: `mailto:?subject=${encodeURIComponent(subject)}`
      + `&body=${encodeURIComponent(body)}`,
    whatsapp: `https://wa.me/?text=${encodeURIComponent(text)}`,
    teams: 'https://teams.microsoft.com/share'
      + `?href=${encodeURIComponent(record.url)}`
      + `&msgText=${encodeURIComponent(text)}`,
  };
};
