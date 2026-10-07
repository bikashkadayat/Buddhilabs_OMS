/**
 * Capability parity guard (Phase 203).
 *
 * Phase 203 is a presentation change. It must not move a single permission, and
 * "must not" is worth a test rather than a promise: the sidebar restructure in a
 * later phase moves these capability checks from one file to another, and a
 * silently altered role list there would be a security regression wearing the
 * costume of a UI refactor.
 *
 * This pins the role -> capability map exactly as it stood before Phase A. Any
 * change - adding a role to a capability, removing one, renaming a capability -
 * fails here and has to be justified deliberately.
 */
import { describe, it, expect } from 'vitest';
import { PERMISSIONS, ROLES, ROLE_LABELS, can } from '../services/roles';

/** Frozen snapshot. Do not edit to make a test pass. */
const BASELINE = {
  applyLeave: ['maker', 'checker', 'approver'],
  createMemo: ['maker', 'checker', 'approver'],
  myApplications: ['maker', 'checker', 'approver'],
  myMemos: ['maker', 'checker', 'approver'],
  reviewLeave: ['checker', 'approver', 'admin'],
  allMemos: ['maker', 'checker', 'approver', 'admin'],
  userManagement: ['admin'],
  reports: ['checker', 'approver', 'admin'],
  workforceSelf: ['maker', 'checker', 'approver'],
  workforceTeam: ['checker', 'approver', 'admin'],
  workforceHR: ['approver', 'admin'],
  workforceConflicts: ['checker', 'approver', 'admin'],
  workforceReports: ['checker', 'approver', 'admin'],
  analyticsView: ['checker', 'approver', 'admin'],
  analyticsOrg: ['approver', 'admin'],
  analyticsExport: ['checker', 'approver', 'admin'],
  systemMonitoring: ['approver', 'admin'],
};

/**
 * Capabilities added by a NEW module since Phase 203, each with the phase that
 * added it.
 *
 * A new module needs new capabilities; that is not what this guard is for. What
 * it is for is a capability that CHANGES or DISAPPEARS, and that is still
 * caught: BASELINE below is compared value-for-value, and the key set must be
 * exactly BASELINE plus this list — so adding one silently, without recording it
 * here, still fails.
 */
const ADDED_SINCE = {
  // Phase T1 (Task Management). Creating and handing out tasks is a management
  // verb: the workflow puts Create Task under "HR / Department Head / Admin".
  assignTask: ['checker', 'approver', 'admin'],
  tasksTeam: ['checker', 'approver', 'admin'],
  // Phase TASK-MANAGEMENT-ASANA-MODEL. Creation opened to every role; handing
  // work to somebody else (assignTask) deliberately did NOT move.
  createTask: ['maker', 'checker', 'approver', 'admin'],
  taskReview: ['checker', 'approver', 'admin'],
  tasksAll: ['approver', 'admin'],
  // Phase APM-03b (Appraisal UX). "My Appraisal" is ungated — everybody has
  // one — so only the two management queues appear here.
  appraisalTeam: ['checker', 'approver', 'admin'],
  appraisalHR: ['approver', 'admin'],
  // Phase BOD-ROLE-EXECUTIVE-GOVERNANCE.
  analyticsDevices: ['approver', 'admin'],
  appraisalOverview: ['approver', 'bod', 'admin'],
  executiveDashboard: ['approver', 'bod', 'admin'],
  assetRegisterRead: ['checker', 'approver', 'bod', 'admin'],
};

/**
 * A NEW ROLE, recorded the same way a new capability is (Phase
 * BOD-ROLE-EXECUTIVE-GOVERNANCE).
 *
 * The Board of Directors was added to these existing capabilities and to no
 * others. BASELINE is NOT edited: the comparison below subtracts exactly this
 * list, so the pre-existing roles on every capability are still checked
 * value-for-value, and granting the Board anything further - userManagement,
 * systemMonitoring, workforceHR, assignTask - fails here.
 */
const BOARD_ADDED_TO = [
  'applyLeave', 'createMemo', 'myApplications', 'myMemos', 'reviewLeave', 'allMemos',
  'reports', 'workforceSelf', 'workforceTeam', 'workforceConflicts', 'workforceReports',
  'analyticsView', 'analyticsOrg', 'analyticsExport', 'tasksTeam',
  // Phase TASK-MANAGEMENT-ASANA-MODEL: the Board keeps its own task list and
  // reads every department's, and still decides none of them.
  'createTask', 'tasksAll',
];

const withoutBoard = (capability, roles) => (BOARD_ADDED_TO.includes(capability)
  ? roles.filter((r) => r !== 'bod') : roles);

describe('capability parity', () => {
  it('defines exactly the capabilities it defined before Phase 203, plus the '
    + 'recorded additions', () => {
    expect(Object.keys(PERMISSIONS).sort()).toEqual(
      [...Object.keys(BASELINE), ...Object.keys(ADDED_SINCE)].sort());
  });

  it('grants each recorded addition to exactly the roles it records', () => {
    for (const [capability, roles] of Object.entries(ADDED_SINCE)) {
      expect([capability, withoutBoard(capability, [...PERMISSIONS[capability]]).sort()])
        .toEqual([capability, [...roles].sort()]);
    }
  });

  it('grants each capability to exactly the same roles', () => {
    for (const [capability, roles] of Object.entries(BASELINE)) {
      expect([capability, withoutBoard(capability, [...PERMISSIONS[capability]]).sort()])
        .toEqual([capability, [...roles].sort()]);
    }
  });

  it('grants the Board exactly the recorded capabilities, and no admin one', () => {
    const granted = Object.entries(PERMISSIONS)
      .filter(([, roles]) => roles.includes('bod')).map(([c]) => c).sort();
    expect(granted).toEqual([...BOARD_ADDED_TO,
      'appraisalOverview', 'executiveDashboard', 'assetRegisterRead'].sort());
    for (const capability of ['userManagement', 'systemMonitoring', 'workforceHR',
      'analyticsDevices', 'assignTask', 'appraisalHR', 'taskReview']) {
      expect(can('bod', capability), capability).toBe(false);
    }
  });

  it('keeps the roles in seniority order, with their business-facing labels', () => {
    expect(ROLES).toEqual(['maker', 'checker', 'approver', 'bod', 'admin']);
    expect(ROLE_LABELS).toEqual({
      maker: 'Employee',
      checker: 'Department Head',
      approver: 'HR',
      bod: 'Board of Directors',
      admin: 'Admin',
    });
  });

  it('still denies an unknown role and an unknown capability', () => {
    expect(can('nobody', 'reviewLeave')).toBe(false);
    expect(can('admin', 'notACapability')).toBe(false);
  });

  it('keeps Admin out of personal self-service, as the config intends', () => {
    for (const capability of ['applyLeave', 'createMemo', 'myApplications', 'myMemos']) {
      expect(can('admin', capability)).toBe(false);
    }
  });
});
