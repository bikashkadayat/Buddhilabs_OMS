import React from 'react';
import CategoryBalances from '../../components/leave/CategoryBalances';

/**
 * Leave Balance — the entitlement cards on a page of their own.
 *
 * The same component the dashboard shows, given its own route because the
 * policy menu names it as a destination: "how much leave do I have left" is a
 * question people navigate to directly, not something they should have to
 * remember is halfway down a dashboard.
 *
 * Every figure comes from /leaves/my-entitlements/, which derives from the
 * EntitlementRule matrix — the same source as the published policy table, so
 * the two cannot disagree.
 */
const LeaveBalance = () => (
  <div className="page">
    <div className="pg-head">
      <div className="pg-head-left">
        <div className="pg-breadcrumb">Leave Management</div>
        <div className="pg-title">Leave Balance</div>
        <div className="pg-desc">
          Your entitlement for the current year, by leave category.
        </div>
      </div>
    </div>
    <CategoryBalances />
  </div>
);

export default LeaveBalance;
