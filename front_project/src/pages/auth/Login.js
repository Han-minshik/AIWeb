import React, { useState } from 'react';
import { login } from '../../services/auth/authService';
import '../../styles/pages/login.css';

export default function Login() {
  const [id, setId] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const onSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!id.trim() || !password.trim()) {
      setError('Please enter your ID and Password.');
      return;
    }
    setSubmitting(true);
    try {
      await login(id, password);
      window.location.replace('/');
    } catch (err) {
      setError(err.message || 'An error occurred while logging in.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <main className="login-card" role="main" aria-label="Login">
        <header className="login-header">
          <h1 className="login-title">Welcome to SPACE AI</h1>
          <p className="login-subtitle">Please log in to use the service</p>
        </header>

        <form className="login-form" onSubmit={onSubmit} aria-label="Login Form">
          <div className="login-field">
            <label className="login-label" htmlFor="login-id">
              ID
            </label>
            <input
              id="login-id"
              name="id"
              type="text"
              autoComplete="username"
              className="login-input"
              placeholder="Please enter your ID"
              value={id}
              onChange={(e) => setId(e.target.value)}
              disabled={submitting}
              required
            />
          </div>

          <div className="login-field">
            <label className="login-label" htmlFor="login-password">
              Password
            </label>
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              className="login-input"
              placeholder="Please enter your Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </div>

          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          <button className="login-button" type="submit" disabled={submitting}>
            {submitting ? 'Logging in...' : 'Login'}
          </button>
        </form>
      </main>
    </div>
  );
}
