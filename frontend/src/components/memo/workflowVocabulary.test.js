import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ROLE_TYPES, ROLE_ACTIONS, roleAction, roleGuidance } from './memoLabels';

/**
 * Phase MEMO-WORKFLOW-UX-HARDENING.
 *
 * The action button rendered the ROLE NAME for every role except approver, so
 * a reviewer was offered a button that said "Reviewer" beside one that said
 * "Reject" — a noun and a verb, with nothing on screen saying the noun was how
 * you pass the memo on. Three of the four roles hit it, and a memo parked at
 * one of them is indistinguishable from a workflow that has stopped.
 *
 * These pin the vocabulary rather than the styling: a button that names the
 * reader's role instead of the action is the regression.
 */
const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(join(here, '..', '..', 'pages', 'memo', 'MemoDetail.jsx'), 'utf8');

describe('workflow action vocabulary', () => {
  it('gives every role an action, a title and an instruction', () => {
    for (const role of ROLE_TYPES) {
      const g = ROLE_ACTIONS[role];
      expect(g, `no guidance for ${role}`).toBeTruthy();
      for (const field of ['title', 'instruction', 'action', 'done']) {
        expect(g[field], `${role}.${field}`).toBeTruthy();
      }
    }
  });

  it('never labels the button with the role name', () => {
    // The exact defect: roleAction('reviewer') must not be "Reviewer".
    for (const role of ROLE_TYPES) {
      const label = roleAction(role).toLowerCase();
      expect(label, `${role} button is its own role name`).not.toBe(role);
      expect(label).not.toBe(`${role}r`);
    }
    expect(roleAction('reviewer')).toBe('Mark as Reviewed');
    expect(roleAction('recommender')).toBe('Recommend');
    expect(roleAction('supporter')).toBe('Support');
    expect(roleAction('approver')).toBe('Approve');
  });

  it('falls back to a verb, not a noun, for an unknown role', () => {
    // A role added server-side before the client knows about it must not
    // render `undefined` or a bare code as a button label.
    expect(roleAction('delegate')).toBe('Confirm');
    expect(roleGuidance('delegate').instruction).toMatch(/review/i);
  });

  it('the detail page renders the verb, not roleTypeLabel, on the button', () => {
    // Guards the call site as well as the vocabulary: the map is useless if
    // the button goes back to reading the role label.
    expect(detail).toMatch(/\{roleAction\(myStep\.role_type\)\}/);
    expect(detail, 'the old ternary is back')
      .not.toMatch(/role_type === 'approver' \? 'Approve' : roleTypeLabel/);
  });

  it('shows the step, what is required, and what to do first', () => {
    expect(detail).toMatch(/memo-step-eyebrow/);
    expect(detail).toMatch(/roleGuidance\(myStep\.role_type\)\.title/);
    expect(detail).toMatch(/roleGuidance\(myStep\.role_type\)\.instruction/);
    // "Step 2 of 4" — position in the chain, not just the role.
    expect(detail).toMatch(/Step \{myStep\.sequence\} of/);
  });
});
