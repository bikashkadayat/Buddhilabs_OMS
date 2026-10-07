import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { adminLeaveService } from '../../../services/adminLeaveService';
import { days } from '../../../utils/leaveFormat';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';

const initials = (name) => (name || '?').split(' ').map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();

const EmployeeList = () => {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [role, setRole] = useState('');
  const [department, setDepartment] = useState('');

  const params = {};
  if (search) params.search = search;
  if (role) params.role = role;
  if (department) params.department = department;

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['admin-employees', search, role, department],
    queryFn: () => adminLeaveService.getEmployees(params),
  });

  const rows = data?.results ?? [];

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Employees</h2>
          <div className="lr-page-sub">Leave usage and attendance across the organization</div>
        </div>
      </div>

      <div className="lr-filter-bar">
        <input
          type="search" placeholder="Search name or email" value={search}
          onChange={(e) => setSearch(e.target.value)} aria-label="Search employees"
        />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Filter by role">
          <option value="">All roles</option>
          <option value="maker">Maker</option>
          <option value="checker">Checker</option>
          <option value="approver">Approver</option>
          <option value="admin">Admin</option>
        </select>
        <input
          type="text" placeholder="Department code" value={department}
          onChange={(e) => setDepartment(e.target.value.toUpperCase())} aria-label="Filter by department"
        />
      </div>

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState message="No employees match these filters." ctaTo={undefined} />
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead>
              <tr>
                <th scope="col">Employee</th><th scope="col">Department</th>
                <th scope="col">Role</th><th scope="col">Used</th>
                <th scope="col">Available</th><th scope="col">Attendance</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr
                  key={e.id} className="lr-month-row" tabIndex={0}
                  onClick={() => navigate(`/admin/leaves/employees/${e.id}`)}
                  onKeyDown={(k) => { if (k.key === 'Enter') navigate(`/admin/leaves/employees/${e.id}`); }}
                >
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span className="lr-avatar" aria-hidden="true">{initials(e.full_name)}</span>
                      <div>
                        <div style={{ fontWeight: 600 }}>{e.full_name}</div>
                        <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{e.email}</div>
                      </div>
                    </div>
                  </td>
                  <td>{e.department || '—'}</td>
                  <td style={{ textTransform: 'capitalize' }}>{e.role}</td>
                  <td>{days(e.used_days)}</td>
                  <td>{days(e.available_days)}</td>
                  <td><AttendanceIndicator percentage={e.attendance_percentage} /></td>
                  <td>{e.is_active ? 'Active' : 'Inactive'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default EmployeeList;
