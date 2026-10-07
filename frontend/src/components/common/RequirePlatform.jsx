import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';

/**
 * Route guard for the Platform Admin Console (Phase S6).
 *
 * SEPARATE FROM RequireAuth, not an option on it. RequireAuth gates on `role`
 * — maker / checker / approver / bod / admin — which is a TENANT role. A
 * platform operator has none of those: they belong to no organization at all.
 * Adding a sixth "role" would have made `admin` and platform staff comparable
 * on one scale, and the whole point of the split is that they are not.
 *
 * This is a convenience, not a security boundary. Every console endpoint is
 * refused server-side by IsPlatformStaff and again by
 * console.require_platform; what this does is keep a tenant administrator from
 * being shown a console shell full of failed requests.
 */
const RequirePlatform = ({ children }) => {
  const { isAuthenticated, isPlatformStaff, loading } = useAuth();
  const location = useLocation();

  if (loading) return <div className="page">Loading…</div>;
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  // A tenant user is sent to their own workspace, not to /unauthorized: they
  // are not forbidden from the product, they are in the wrong part of it.
  if (!isPlatformStaff) return <Navigate to="/" replace />;

  return children;
};

export default RequirePlatform;
