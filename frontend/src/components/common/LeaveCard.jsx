import React from 'react';
import Badge from './Badge';

const LeaveCard = ({ leave }) => {
  const initials = leave.employee
    .split(' ')
    .map(n => n[0])
    .join('')
    .substring(0, 2)
    .toUpperCase();

  const getCardStyle = () => {
    if (leave.status === 'approved') {
      return { background: '#d1fae5', border: '1px solid #10b981' };
    }
    if (leave.status === 'rejected') {
      return { background: '#fee2e2', border: '1px solid #ef4444' };
    }
    return {};
  };

  return (
    <div className="leave-card" style={getCardStyle()}>
      <div className="lc-av">{initials}</div>
      <div className="lc-info">
        <div className="lc-name">{leave.employee}</div>
        <div className="lc-type">{leave.type}</div>
        <div className="lc-dates">
          {leave.start} to {leave.end} ({leave.days} days)
        </div>
      </div>
      <div className="lc-status">
        <Badge status={leave.status} />
      </div>
    </div>
  );
};

export default LeaveCard;
