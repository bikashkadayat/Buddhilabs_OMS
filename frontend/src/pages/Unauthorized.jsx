import React from 'react';
import { useNavigate } from 'react-router-dom';

const Unauthorized = () => {
  const navigate = useNavigate();
  return (
    <div className="page" style={{ textAlign: 'center', paddingTop: 80 }}>
      <h2>403 · Not authorized</h2>
      <p className="lr-page-sub">You do not have permission to view this page.</p>
      <button type="button" className="lr-btn lr-btn-primary" onClick={() => navigate('/')} style={{ marginTop: 16 }}>
        Back to dashboard
      </button>
    </div>
  );
};

export default Unauthorized;
