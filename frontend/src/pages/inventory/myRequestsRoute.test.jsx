import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Assert against the REAL route table, not a copy of it — a duplicated role list
// here would keep passing after someone edited App.jsx. (vitest runs with the
// frontend package root as cwd.)
const appSource = readFileSync(resolve(process.cwd(), 'src/App.jsx'), 'utf8');

const routeLine = (path) =>
  appSource.split('\n').find((l) => l.includes(`path="${path}"`)) || '';

const allowedRoles = (path) => {
  const m = routeLine(path).match(/allowedRoles=\{\[([^\]]*)\]\}/);
  return m ? m[1].split(',').map((s) => s.trim().replace(/['"]/g, '')).filter(Boolean) : null;
};

describe('inventory route access (App.jsx)', () => {
  // Regression: 'admin' was missing from my-requests, so an admin could not see
  // their OWN take-out requests even though the API lets any authenticated user
  // raise one.
  it('lets admins see their own take-out requests', () => {
    expect(allowedRoles('inventory/my-requests')).toContain('admin');
  });

  it('still lets employees and managers see their own take-out requests', () => {
    const roles = allowedRoles('inventory/my-requests');
    expect(roles).toEqual(expect.arrayContaining(['maker', 'checker', 'approver']));
  });

  it('keeps manager-only inventory screens closed to plain employees', () => {
    for (const path of ['inventory', 'inventory/items/:id', 'inventory/approvals']) {
      expect(allowedRoles(path)).not.toContain('maker');
    }
  });
});
