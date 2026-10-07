/** Small formatting helpers for leave records (Decimal strings -> numbers). */

/** Coerce a backend Decimal string (or number) to a Number, defaulting to 0. */
export const num = (value) => {
  const n = typeof value === 'number' ? value : parseFloat(value);
  return Number.isFinite(n) ? n : 0;
};

/** Format a day count, dropping a trailing ".0" (e.g. "3.5", "2"). */
export const days = (value) => {
  const n = num(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
};

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export const monthName = (m) => MONTH_NAMES[(m - 1 + 12) % 12] || '';

/** Build the last `count` years ending at the current year (descending). */
export const recentYears = (count = 4) => {
  const current = new Date().getFullYear();
  return Array.from({ length: count }, (_, i) => current - i);
};
