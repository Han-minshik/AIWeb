import React from 'react';
import ReactDOM from 'react-dom/client';

import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import { reportClientError } from './services/errors/errorReporter';

import './styles/base/token.css';
import './styles/base/reset.css';
import './styles/base/globals.css';
import './styles/layout/layout.css';

const root = ReactDOM.createRoot(document.getElementById('root'));

if (typeof window !== 'undefined') {
  window.addEventListener('error', (event) => {
    reportClientError(event?.error || event?.message || 'Window error', {
      source: 'window-error',
      path: window.location?.pathname || '',
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    reportClientError(event?.reason || 'Unhandled promise rejection', {
      source: 'unhandledrejection',
      path: window.location?.pathname || '',
    });
  });
}

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

reportWebVitals();
