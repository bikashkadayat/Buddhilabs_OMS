import React from 'react';

const Row = React.memo(function DepartmentRow({ row }) {
  const c = row.counts || {};
  return (
    <tr>
      <td className="wf-strong">{row.department}</td>
      <td>{row.headcount}</td>
      <td className="wf-tone-ok">{c.present || 0}</td>
      <td className="wf-tone-warn">{c.late || 0}</td>
      <td className="wf-tone-warn">{c.half_day || 0}</td>
      <td className="wf-tone-accent">{c.wfh || 0}</td>
      <td className="wf-tone-info">{c.on_leave || 0}</td>
      <td className="wf-tone-bad">{c.absent || 0}</td>
      <td><b>{row.present_now}</b></td>
    </tr>
  );
});

/** Per-department status counts. Rows are memoised so polling does not
 *  re-render every cell in a long list. */
const DepartmentSummaryTable = ({ rows = [] }) => {
  if (!rows.length) return <p className="wf-muted">No departments to show.</p>;
  return (
    <div className="wf-table-scroll">
      <table className="wf-table">
        <thead>
          <tr>
            <th>Department</th><th>Headcount</th><th>Present</th><th>Late</th>
            <th>Half day</th><th>WFH</th><th>On leave</th><th>Absent</th>
            <th>Present now</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Row key={row.department_id || row.department} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default DepartmentSummaryTable;
