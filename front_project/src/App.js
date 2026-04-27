import React, { useEffect, useState } from 'react';
import './App.css';

import Loading from './pages/Loading';

import { BrowserRouter, useLocation } from 'react-router-dom';

import Header from './components/Header';
import { KBProvider } from './contexts/KBContext';
import { HistoryProvider } from './contexts/HistoryContext';
import ToastProvider from './contexts/ToastContext';
import AppRouter from './routers/AppRouter';

import './styles/components/menu.css';
import './styles/components/modal.css';

import HistoryModal from './components/history/HistoryModal';
import './styles/components/history-modal.css';

import QAEventBridge from './components/ai/QAEventBridge';

function AppInner() {
  const [ready, setReady] = useState(false);
  const [globalError, setGlobalError] = useState(null);
  const { pathname } = useLocation();

  useEffect(() => {
    const t = setTimeout(() => setReady(true), 600);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('ui_theme');
      const next = saved || 'light';
      document.documentElement.setAttribute('data-theme', next);
      if (!saved) localStorage.setItem('ui_theme', next);
      window.dispatchEvent(new CustomEvent('ui:theme-change', { detail: { theme: next } }));
    } catch {}
  }, []);

  useEffect(() => {
    const onAppError = (event) => {
      const detail = event?.detail || {};
      const message = String(detail?.message || 'Unexpected error occurred.');
      const requestId = String(detail?.requestId || '').trim();
      setGlobalError({ message, requestId, at: Date.now() });
    };

    window.addEventListener('app:error', onAppError);
    return () => window.removeEventListener('app:error', onAppError);
  }, []);

  if (!ready) return <Loading />;

  const isAdminPage = pathname.startsWith('/admin');
  const isLoginPage = pathname.startsWith('/login');

  return (
    <div className="app-root">
      {globalError && (
        <div className="global-error-banner" role="alert">
          <div className="global-error-text">
            <strong>Error:</strong> {globalError.message}
            {globalError.requestId ? ` (Request ID: ${globalError.requestId})` : ''}
          </div>
          <button type="button" className="global-error-close" onClick={() => setGlobalError(null)}>
            Close
          </button>
        </div>
      )}
      {isAdminPage && !isLoginPage && <Header />}

      <AppRouter />

      {!isLoginPage && (
        <>
          <QAEventBridge />
          <HistoryModal />
        </>
      )}

      <ToastProvider />
    </div>
  );
}

export default function App() {
  return (
    <KBProvider>
      <HistoryProvider>
        <BrowserRouter>
          <AppInner />
        </BrowserRouter>
      </HistoryProvider>
    </KBProvider>
  );
}
