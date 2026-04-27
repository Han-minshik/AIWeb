import React from 'react';
import { NavLink } from 'react-router-dom';

import PeopleIcon from '/src/assets/icons/people.svg';
import FolderOpenIcon from '/src/assets/icons/folder2-open.svg';
import GraphUpIcon from '/src/assets/icons/graph-up.svg';

const nav = [
  { to: '/admin/users', label: 'Users', icon: PeopleIcon },
  { to: '/admin/project-files', label: 'KB Management', icon: FolderOpenIcon },
  { to: '/admin/sessions-feedback', label: 'Session/Feedback', icon: GraphUpIcon },
];

export default function AdminSidebar({ activePath }) {
  return (
    <div className="sidebar-wrap">
      <NavLink to="/" className="sidebar-brand">SPACE AI</NavLink>

      <nav className="sidebar-nav">
        {nav.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              'sidebar-link' + (isActive ? ' active' : '')
            }
          >
            <span className="sidebar-icon">
              <img src={item.icon} alt="" aria-hidden="true" />
            </span>
            <span className="sidebar-text">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
