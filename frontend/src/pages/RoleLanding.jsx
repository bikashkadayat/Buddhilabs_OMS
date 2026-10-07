import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const RoleLanding = () => {
  const { role, loading } = useAuth();

  if (loading) {
    return (
      <div className="page" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
        <div>Loading your workspace...</div>
      </div>
    );
  }

  if (!role) {
    return <Navigate to="/login" replace />;
  }

  if (role === 'maker') {
    return <Navigate to="/leave" replace />
  }

  if (role === 'checker') {
    return <Navigate to="/leave/pending" replace />;
  }

  if (role === 'approver') {
    return <Navigate to="/leave" replace />;
  }

  if (role === 'admin') {
    return <Navigate to="/leave" replace />;
  }

  return <Navigate to="/leave" replace />;
};

export default RoleLanding;
