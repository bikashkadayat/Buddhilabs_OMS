import React from 'react';
import LeavePolicyWidget from '../../components/leave/LeavePolicyWidget';

/**
 * Leave Policy — the organisation's entitlement table.
 *
 * Rendered from /leaves/leave-policy/, which reads the live EntitlementRule
 * rows rather than a hard-coded table. That is deliberate: a policy page that
 * restates the rules in its own markup drifts away from the balances people
 * are actually granted, and nobody notices until someone is short a day.
 */
const LeavePolicy = () => (
  <div className="page">
    <div className="pg-head">
      <div className="pg-head-left">
        <div className="pg-breadcrumb">Leave Management</div>
        <div className="pg-title">Leave Policy</div>
        <div className="pg-desc">
          Entitlements by category. Your own category is highlighted.
        </div>
      </div>
    </div>
    <LeavePolicyWidget />
  </div>
);

export default LeavePolicy;
