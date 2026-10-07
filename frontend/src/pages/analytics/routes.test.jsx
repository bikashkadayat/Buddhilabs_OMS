import { describe, it, expect } from 'vitest';
import { PERMISSIONS, can } from '../../services/roles';
import { ANALYTICS_REPORTS, reportTypeByKey } from '../../services/reportService';
import { ANALYTICS_EXPORTS } from '../../services/analyticsService';

/**
 * Route guards, asserted through the permission map that drives both the router
 * and the sidebar — so a route and its nav entry cannot drift apart. Mirrors the
 * backend matrix in `analytics/tests/test_permissions.py`; the API is the real
 * boundary and these guards only avoid rendering a page that would 403.
 */
const ROLES = ['maker', 'checker', 'approver', 'bod', 'admin'];
// Phase BOD-ROLE-EXECUTIVE-GOVERNANCE added the Board of Directors to some of the
// capabilities pinned below. The pins still check every PRE-EXISTING role exactly:
// the Board is removed before comparing, and its own grants are asserted
// separately, so neither half can change unnoticed.
const withoutBoard = (roles) => roles.filter((r) => r !== 'bod');


describe('Analytics permissions', () => {
  it('gives employees no analytics at all', () => {
    ['analyticsView', 'analyticsOrg', 'analyticsExport'].forEach((action) => {
      expect(can('maker', action)).toBe(false);
    });
  });

  it('gives department heads the scoped tier but not the org tier', () => {
    expect(can('checker', 'analyticsView')).toBe(true);
    expect(can('checker', 'analyticsExport')).toBe(true);
    expect(can('checker', 'analyticsOrg')).toBe(false);
  });

  it('gives HR and admin everything', () => {
    ['approver', 'admin'].forEach((role) => {
      expect(can(role, 'analyticsView')).toBe(true);
      expect(can(role, 'analyticsOrg')).toBe(true);
      expect(can(role, 'analyticsExport')).toBe(true);
    });
  });

  it('defines every analytics permission it references', () => {
    ['analyticsView', 'analyticsOrg', 'analyticsExport'].forEach((action) => {
      expect(PERMISSIONS[action]).toBeDefined();
      expect(PERMISSIONS[action].every((role) => ROLES.includes(role))).toBe(true);
    });
  });

  it('leaves the Phase 9 permissions untouched', () => {
    expect(PERMISSIONS.workforceHR).toEqual(['approver', 'admin']);
    expect(withoutBoard(PERMISSIONS.workforceTeam)).toEqual(['checker', 'approver', 'admin']);
    expect(withoutBoard(PERMISSIONS.applyLeave)).toEqual(['maker', 'checker', 'approver']);
  });

  it('gives the Board the organisation tier, but not device infrastructure', () => {
    ['analyticsView', 'analyticsOrg', 'analyticsExport'].forEach((action) => {
      expect(can('bod', action), action).toBe(true);
    });
    expect(can('bod', 'analyticsDevices')).toBe(false);
    expect(can('bod', 'systemMonitoring')).toBe(false);
  });
});

describe('Analytics export catalogue', () => {
  it('registers all five exports', () => {
    expect(ANALYTICS_REPORTS).toHaveLength(5);
    expect(ANALYTICS_EXPORTS).toHaveLength(5);
  });

  it('agrees with the report hub about types and formats', () => {
    ANALYTICS_EXPORTS.forEach((entry) => {
      const hubEntry = reportTypeByKey(entry.type);
      expect(hubEntry, `${entry.type} is missing from the hub catalogue`).toBeTruthy();
      expect(hubEntry.formats).toContain(entry.format);
    });
  });

  it('offers exactly one format per export, matching the backend', () => {
    const expected = {
      executive_summary: 'pdf',
      department_analytics: 'pdf',
      attendance_analytics: 'excel',
      overtime_analytics: 'excel',
      workforce_analytics: 'csv',
    };
    ANALYTICS_REPORTS.forEach((report) => {
      expect(report.formats).toEqual([expected[report.key]]);
    });
  });

  it('marks the executive summary as organisation-only in both catalogues', () => {
    // It is an organisation-wide document by definition; a department-scoped
    // copy would carry a title its contents do not support.
    expect(ANALYTICS_REPORTS.find((r) => r.key === 'executive_summary').orgOnly).toBe(true);
    expect(ANALYTICS_EXPORTS.find((e) => e.type === 'executive_summary').orgOnly).toBe(true);
    expect(ANALYTICS_EXPORTS.filter((e) => e.orgOnly)).toHaveLength(1);
  });

  it('does not disturb the Phase 8 or Phase 9 catalogues', () => {
    expect(reportTypeByKey('employee_register')).toBeTruthy();
    expect(reportTypeByKey('department_attendance')).toBeTruthy();
    expect(reportTypeByKey('not_a_report')).toBeFalsy();
  });
});
