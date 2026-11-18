import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { me } from '../../services/auth/authService';

export default function AdminGuard({ children }) {
  const [loading, setLoading] = useState(true);
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const u = await me();
        if (!alive) return;
        setAllowed(!!u?.is_admin);
      } catch {
        if (!alive) return;
        setAllowed(false);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="panel" style={{ height: '100%' }}>
        <div className="panel-head">
          <div className="panel-title">Admin</div>
        </div>
        <div className="card">
          <div className="loading">Checking permissions...</div>
        </div>
      </div>
    );
  }

  if (!allowed) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
