import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';

const FIRST_LOGIN_PATH = '/auth/first-login-change-password';

/**
 * Route guard. Keeps /login as the single entry point, enforces the first-login
 * password change, and optionally restricts by role.
 * @param {{ children: React.ReactNode, allowedRoles?: string[] }} props
 */
const RequireAuth = ({ children, allowedRoles }) => {
  const { isAuthenticated, isPlatformStaff, mustChangePassword, role, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="page">Loading authentication...</div>;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // First-login password change cannot be dismissed.
  if (mustChangePassword && location.pathname !== FIRST_LOGIN_PATH) {
    return <Navigate to={FIRST_LOGIN_PATH} replace />;
  }

  // A PLATFORM OPERATOR BELONGS IN THE CONSOLE, and this is the mirror of
  // `RequirePlatform`, which already sends a tenant user who reaches
  // /platform back to "/". Nothing sent an operator the other way, so an
  // operator who typed the root URL, or followed a bookmark, mounted the
  // TENANT application -- which immediately calls tenant endpoints with a
  // session that is bound to no tenant. `/api/v1/notifications/` answers
  // HTTP 500 for exactly that reason (`TenantScopeMissing`, which is
  // deliberately loud: it means a scoped read happened with nothing bound).
  //
  // So the operator saw a half-broken copy of a customer's product. Found by
  // calling the endpoint as an operator while checking that the new profile
  // page worked, not by a test.
  if (isPlatformStaff) {
    return <Navigate to="/platform" replace />;
  }

  if (allowedRoles && !allowedRoles.includes(role)) {
    return <Navigate to="/unauthorized" replace />;
  }

  return children;
};

export default RequireAuth;
