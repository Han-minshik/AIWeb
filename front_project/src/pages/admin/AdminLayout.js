import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import AdminSidebar from '../../components/admin/AdminSidebar';
import AdminTopbar from '../../components/admin/AdminTopbar';
import '../../styles/admin/admin.css';

export default function AdminLayout() {
  const { pathname } = useLocation();

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <AdminSidebar activePath={pathname} />
      </aside>

      <main className="admin-main">
        <header className="admin-topbar">
          <AdminTopbar />
        </header>

        <section className="admin-content">
          <Outlet />
        </section>
      </main>
    </div>
  );
}
