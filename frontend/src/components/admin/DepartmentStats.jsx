import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { adminService } from '../../services/adminService';
import { Building2, Users, UserCheck, FileText, Clock } from 'lucide-react';

// Department statistics for the admin dashboard: per-department employee count,
// active employees, leave requests, and pending reviews.
const ROWS = [
  { key: 'employee_count', label: 'Employees', Icon: Users, color: 'var(--brand-blue)' },
  { key: 'active_employees', label: 'Active', Icon: UserCheck, color: 'var(--success)' },
  { key: 'leave_requests', label: 'Leave requests', Icon: FileText, color: 'var(--text-secondary)' },
  { key: 'pending_reviews', label: 'Pending reviews', Icon: Clock, color: 'var(--warning)' },
];

const DepartmentStats = () => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['admin-dept-stats'],
    queryFn: () => adminService.getStats(),
  });

  const depts = data?.by_department || [];
  if (isError || (!isLoading && depts.length === 0)) return null;

  return (
    <div className="table-card ds-card" style={{ padding: 24, marginBottom: 32 }}>
      <div className="ds-head">
        <Building2 size={20} color="var(--brand-blue)" />
        <h3>Department Statistics</h3>
      </div>

      {isLoading ? (
        <div style={{ padding: 24, color: 'var(--text-muted)' }}>Loading department statistics…</div>
      ) : (
        <div className="ds-grid">
          {depts.map((d) => (
            <div key={d.id} className="ds-item">
              <div className="ds-item-head">
                <span className="ds-name">{d.department}</span>
                <span className="ds-code">{d.code}</span>
              </div>
              <div className="ds-rows">
                {ROWS.map(({ key, label, Icon, color }) => (
                  <div key={key} className="ds-row">
                    <span className="ds-row-label"><Icon size={14} color={color} /> {label}</span>
                    <span className="ds-row-val">{d[key]}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default DepartmentStats;
