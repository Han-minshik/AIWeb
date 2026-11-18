import React, { useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { me } from '../services/auth/authService';

function isAdminUser(user) {
  if (!user) return false;
  const role = String(user.role ?? user.user_role ?? '').toLowerCase();
  const roles = Array.isArray(user.roles ?? user.scopes)
    ? (user.roles ?? user.scopes).map((v) => String(v).toLowerCase())
    : [];
  return Boolean(user.is_admin === true || user.admin === true || role === 'admin' || roles.includes('admin'));
}

export default function RequireAdmin({ children }) {
  const location = useLocation();
  const [state, setState] = useState({ loading: true, allowed: false, authenticated: false });

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const user = await me();
        if (!alive) return;
        setState({ loading: false, allowed: isAdminUser(user), authenticated: true });
      } catch {
        if (!alive) return;
        setState({ loading: false, allowed: false, authenticated: false });
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  if (state.loading) {
    return (
      <div className="panel" style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <div className="loading">Checking permissions...</div>
      </div>
    );
  }

  if (!state.authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (!state.allowed) {
    return <Navigate to="/" replace />;
  }

  return children;
}
