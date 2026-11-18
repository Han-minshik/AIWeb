import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import Main from '../pages/Main';
import Login from '../pages/auth/Login';
import AdminLayout from '../pages/admin/AdminLayout';
import UsersPage from '../pages/admin/UsersPage';
import ProjectFilesPage from '../pages/admin/ProjectFilesPage';
import SessionsFeedbackPage from '../pages/admin/SessionsFeedbackPage';

import RequireAuth from './RequireAuth';
import RequireAdmin from './RequireAdmin';

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Main />
          </RequireAuth>
        }
      />

      <Route
        path="/admin"
        element={
          <RequireAuth>
            <RequireAdmin>
              <AdminLayout />
            </RequireAdmin>
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="users" replace />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="project-files" element={<ProjectFilesPage />} />
        <Route path="sessions-feedback" element={<SessionsFeedbackPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
