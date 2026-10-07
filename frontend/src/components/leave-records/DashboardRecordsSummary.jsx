import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useMyBalances, useMonthlySummaries, useWeeklySummaries } from '../../hooks/useLeaveRecords';
import { days } from '../../utils/leaveFormat';
import BalanceCard from './BalanceCard';
import AttendanceIndicator from './AttendanceIndicator';
import { Skeleton } from './States';

/**
 * Compact leave-records summary for the employee dashboard (Phase 6).
 * Additive widget: quick balances + this-month / this-week cards + a link to
 * the full history page. Fails soft (renders nothing) if the records API errors.
 */
const DashboardRecordsSummary = () => {
  const navigate = useNavigate();
  const year = new Date().getFullYear();
  const month = new Date().getMonth() + 1;

  const { data: balances = [], isLoading, isError } = useMyBalances(year);
  const { data: monthly = [] } = useMonthlySummaries(year);
  const { data: weekly = [] } = useWeeklySummaries(year);

  if (isError) return null;

  const thisMonth = monthly.find((m) => m.month === month);
  const thisWeek = [...weekly].sort((a, b) => b.week_number - a.week_number)[0];

  return (
    <section aria-label="Leave records summary" style={{ marginBottom: 32 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h3 style={{ fontSize: 'var(--fs-h2)', fontWeight: 700, color: 'var(--text-primary)' }}>
          Leave Records
        </h3>
        <button
          type="button"
          onClick={() => navigate('/leaves/my-history')}
          style={{ background: 'none', border: 'none', color: 'var(--brand-blue)', fontSize: 'var(--fs-sm)', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
        >
          View full history <ArrowRight size={14} />
        </button>
      </div>

      {isLoading ? (
        <Skeleton rows={1} height={120} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
          {balances.slice(0, 3).map((b) => (
            <BalanceCard
              key={b.leave_type_code}
              compact
              leaveType={b.leave_type_name || b.leave_type_code}
              entitled={b.entitled_days}
              used={b.used_days}
              pending={b.pending_days}
              available={b.available_days}
              carriedForward={b.carried_forward_days}
            />
          ))}

          <div className="lr-balance-card lr-balance-compact">
            <div className="lr-bc-type">This month</div>
            <div className="lr-bc-available">
              <span className="lr-bc-available-num">{thisMonth ? days(thisMonth.total_leave_days) : '0'}</span>
              <span className="lr-bc-available-lbl">leave days</span>
            </div>
            {thisMonth && <AttendanceIndicator percentage={thisMonth.attendance_percentage} />}
          </div>

          <div className="lr-balance-card lr-balance-compact">
            <div className="lr-bc-type">This week</div>
            <div className="lr-bc-available">
              <span className="lr-bc-available-num">{thisWeek ? days(thisWeek.total_leave_days) : '0'}</span>
              <span className="lr-bc-available-lbl">leave days</span>
            </div>
            {thisWeek && <AttendanceIndicator percentage={thisWeek.attendance_percentage} />}
          </div>
        </div>
      )}
    </section>
  );
};

export default DashboardRecordsSummary;
