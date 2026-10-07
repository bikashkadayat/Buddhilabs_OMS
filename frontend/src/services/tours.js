/**
 * Which guided tour a person gets, and how to start it again.
 * Kept apart from the component so the component file exports only a
 * component (fast refresh).
 */
export const tourFor = (user, role) => {
  if (user?.is_platform_staff) return 'platform';
  if (role === 'admin') return 'admin';
  if (['checker', 'approver', 'bod'].includes(role)) return 'manager';
  return 'employee';
};

/** Start the tour again from anywhere (Help → Take the tour again). */
export const restartTour = () => window.dispatchEvent(new CustomEvent('tour:start'));
