import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { leaveService } from '../../services/leaveService';

/**
 * Today, in one line (Phase 203 / blueprint §03, widget 3).
 *
 * Answers the two questions people currently navigate to find - am I marked
 * present, and how much leave do I have left - without opening a module.
 *
 * Reuses the ['attendance','today'] query key that AttendanceWidget already
 * populates, so the request is shared rather than duplicated when both are on
 * screen. Everything here degrades to an em dash: a strip that cannot load its
 * numbers must not stop Home from rendering the work below it.
 */


const StatusStrip = () => {
  const { data: balances } = useQuery({
    queryKey: ['leaves', 'balances'],
    queryFn: leaveService.getBalances,
    staleTime: 5 * 60_000,
    retry: false,
  });

  const rows = balances?.data ?? [];
  const annual = rows.find((b) => /annual/i.test(b.leave_type || ''));
  const sick = rows.find((b) => /sick/i.test(b.leave_type || ''));

  return (
    <div className="hm-card hm-strip-row hm-strip">
      <span className="hm-strip-lbl">Leave left</span>

      {/* Today's in/out/status now lives in the attendance hero at the top
          of Home. Repeating it here showed the same day twice -- once as
          "Checked in", once as "Half Day" -- in two places that could
          disagree in wording if not in fact. */}
      {(annual || sick) && (
        <span className="hm-strip-seg">
          {annual && <strong>{annual.remaining} annual</strong>}
          {annual && sick && ' · '}
          {sick && <strong>{sick.remaining} sick</strong>}
        </span>
      )}

      <Link to="/my-attendance" className="hm-strip-link">My attendance →</Link>
    </div>
  );
};

export default StatusStrip;
