import React, { useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { me } from '../services/auth/authService';

export default function RequireAuth({ children }) {
  const location = useLocation();
  const [state, setState] = useState({ loading: true, authenticated: false });

  useEffect(() => {
    let alive = true;
    me()
      .then(() => {
        if (alive) setState({ loading: false, authenticated: true });
      })
      .catch(() => {
        if (alive) setState({ loading: false, authenticated: false });
      });
    return () => {
      alive = false;
    };
  }, []);

  if (state.loading) {
    return (
      <div className="panel" style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <div className="loading">Checking session...</div>
      </div>
    );
  }

  if (!state.authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
