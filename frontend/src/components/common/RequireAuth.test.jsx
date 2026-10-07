import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
import { useAuth } from '../../hooks/useAuth';
import RequireAuth from './RequireAuth';

const Protected = () => <div>PROTECTED</div>;

const renderAt = (path, element) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/login" element={<div>LOGIN</div>} />
      <Route path="/auth/first-login-change-password" element={<div>CHANGE PW</div>} />
      <Route path="/unauthorized" element={<div>NO ACCESS</div>} />
      <Route path="/platform" element={<div>CONSOLE</div>} />
      <Route path="/admin" element={element} />
    </Routes>
  </MemoryRouter>,
);

describe('RequireAuth', () => {
  beforeEach(() => vi.clearAllMocks());

  it('redirects unauthenticated users to /login', () => {
    useAuth.mockReturnValue({ isAuthenticated: false, loading: false });
    renderAt('/admin', <RequireAuth><Protected /></RequireAuth>);
    expect(screen.getByText('LOGIN')).toBeInTheDocument();
  });

  it('forces first-login password change', () => {
    useAuth.mockReturnValue({ isAuthenticated: true, mustChangePassword: true, role: 'maker', loading: false });
    renderAt('/admin', <RequireAuth><Protected /></RequireAuth>);
    expect(screen.getByText('CHANGE PW')).toBeInTheDocument();
  });

  it('blocks a role that is not allowed', () => {
    useAuth.mockReturnValue({ isAuthenticated: true, mustChangePassword: false, role: 'maker', loading: false });
    renderAt('/admin', <RequireAuth allowedRoles={['admin']}><Protected /></RequireAuth>);
    expect(screen.getByText('NO ACCESS')).toBeInTheDocument();
  });

  it('renders children for an authorized user', () => {
    useAuth.mockReturnValue({ isAuthenticated: true, mustChangePassword: false, role: 'admin', loading: false });
    renderAt('/admin', <RequireAuth allowedRoles={['admin']}><Protected /></RequireAuth>);
    expect(screen.getByText('PROTECTED')).toBeInTheDocument();
  });
});

describe('RequireAuth and the platform operator', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends a platform operator to the console instead of the tenant app', () => {
    // The mirror of RequirePlatform, which already bounces a tenant user out
    // of /platform. Nothing bounced an operator the other way, so an operator
    // who typed the root URL mounted the TENANT application with a session
    // bound to no tenant -- and the first thing it does is call tenant
    // endpoints. `/api/v1/notifications/` answers 500 for that, by design:
    // `TenantScopeMissing` means a scoped read ran with nothing bound, and it
    // is meant to be loud. The operator just saw a broken page.
    useAuth.mockReturnValue({
      isAuthenticated: true, isPlatformStaff: true, loading: false,
      mustChangePassword: false, role: null,
    });
    renderAt('/admin', <RequireAuth><Protected /></RequireAuth>);
    expect(screen.getByText('CONSOLE')).toBeInTheDocument();
  });

  it('but a forced password change still comes first', () => {
    // An operator created with `must_change_password` must not be able to
    // skip the change by being redirected past it.
    useAuth.mockReturnValue({
      isAuthenticated: true, isPlatformStaff: true, loading: false,
      mustChangePassword: true, role: null,
    });
    renderAt('/admin', <RequireAuth><Protected /></RequireAuth>);
    expect(screen.getByText('CHANGE PW')).toBeInTheDocument();
  });

  it('and an ordinary user is untouched by it', () => {
    useAuth.mockReturnValue({
      isAuthenticated: true, isPlatformStaff: false, loading: false,
      mustChangePassword: false, role: 'admin',
    });
    renderAt('/admin', <RequireAuth><Protected /></RequireAuth>);
    expect(screen.getByText('PROTECTED')).toBeInTheDocument();
  });
});
