import React from 'react';
import { describe, it, expect } from 'vitest';
import { PERMISSIONS, can } from '../../services/roles';
import { WORKFORCE_REPORTS, reportTypeByKey } from '../../services/reportService';

/**
 * Route guards, expressed as the permission map that drives both the router and
 * the sidebar. Keeping the assertion at this level means a route and its nav
 * entry cannot drift apart, and it mirrors the backend permission matrix that
 * Phase 9 already enforces.
 */
const ROLES = ['maker', 'checker', 'approver', 'bod', 'admin'];

describe('Workforce permissions', () => {
  it('gives every non-admin role the self-service portal', () => {
    ['maker', 'checker', 'approver'].forEach((role) => {
      expect(can(role, 'workforceSelf')).toBe(true);
    });
  });

  it('keeps admin out of self-service (they have no personal attendance)', () => {
    expect(can('admin', 'workforceSelf')).toBe(false);
  });

  it('restricts the team dashboard to department heads and above', () => {
    expect(can('maker', 'workforceTeam')).toBe(false);
    expect(can('checker', 'workforceTeam')).toBe(true);
    expect(can('approver', 'workforceTeam')).toBe(true);
    expect(can('admin', 'workforceTeam')).toBe(true);
  });

  it('restricts the command center to HR and admin', () => {
    expect(can('maker', 'workforceHR')).toBe(false);
    expect(can('checker', 'workforceHR')).toBe(false);
    expect(can('approver', 'workforceHR')).toBe(true);
    expect(can('admin', 'workforceHR')).toBe(true);
  });

  it('restricts conflicts and reports to department heads and above', () => {
    ['workforceConflicts', 'workforceReports'].forEach((action) => {
      expect(can('maker', action)).toBe(false);
      expect(can('checker', action)).toBe(true);
      expect(can('approver', action)).toBe(true);
      expect(can('admin', action)).toBe(true);
    });
  });

  it('defines every workforce permission it references', () => {
    ['workforceSelf', 'workforceTeam', 'workforceHR', 'workforceConflicts',
      'workforceReports'].forEach((action) => {
      expect(PERMISSIONS[action]).toBeDefined();
      expect(PERMISSIONS[action].every((r) => ROLES.includes(r))).toBe(true);
    });
  });

  it('leaves the pre-existing permissions untouched', () => {
    // The Board was added to applyLeave and reports deliberately (Phase BOD);
    // every pre-existing role is still checked exactly.
    const withoutBoard = (roles) => roles.filter((r) => r !== 'bod');
    expect(withoutBoard(PERMISSIONS.applyLeave)).toEqual(['maker', 'checker', 'approver']);
    expect(PERMISSIONS.userManagement).toEqual(['admin']);
    expect(withoutBoard(PERMISSIONS.reports)).toEqual(['checker', 'approver', 'admin']);
  });

  it('gives the Board attendance summaries but not the HR command centre', () => {
    expect(can('bod', 'workforceTeam')).toBe(true);
    expect(can('bod', 'workforceReports')).toBe(true);
    expect(can('bod', 'workforceHR')).toBe(false);
    expect(can('bod', 'userManagement')).toBe(false);
  });
});

describe('Workforce report catalog', () => {
  it('lists the eight reports the backend builds', () => {
    expect(WORKFORCE_REPORTS.map((r) => r.key)).toEqual([
      'attendance_vs_leave', 'attendance_vs_wfh', 'overtime_summary',
      'late_arrival_summary', 'shift_utilization', 'department_attendance',
      'comp_off_report', 'monthly_workforce_summary',
    ]);
  });

  it('declares all three formats on each', () => {
    WORKFORCE_REPORTS.forEach((report) => {
      expect(new Set(report.formats)).toEqual(new Set(['excel', 'pdf', 'csv']));
    });
  });

  it('is reachable through the shared lookup without breaking the leave reports', () => {
    expect(reportTypeByKey('overtime_summary').name).toBe('Overtime Summary');
    expect(reportTypeByKey('employee_register').name).toBe('Employee Leave Register');
  });
});
