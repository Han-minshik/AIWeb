import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import '../../styles/components/assistant-catalog.css';

import RobotIcon from '/src/assets/icons/robot-2-line.svg';
import AppsIcon from '/src/assets/icons/apps-2-line.svg';
import QuestionIcon from '/src/assets/icons/question.svg';
import TrainingIcon from '/src/assets/icons/training.svg';

export const ITEMS = [
  { path: '/home', title: 'KB Question & Answer', desc: 'Please refer to the internal/project KB for your answer.', agentId: null },
  {
    path: '/general-qa',
    title: 'General QA',
    desc: 'Ask broad questions and get concise, practical answers across general topics.',
    agentId: null,
  },
  {
    path: '/deep-research',
    title: 'Deep Research',
    desc: 'Dive into complex topics with structured analysis, key points, and detailed context.',
    agentId: null,
  },
];

const ICON_MAP = {
  '/home': RobotIcon,
  '/general-qa': QuestionIcon,
  '/deep-research': TrainingIcon,
};

const fallbackIcon = AppsIcon;

export function resolveAgent(appType, agentId, appId) {
  const normType = typeof appType === 'string' ? appType.trim() : '';
  const upType = normType ? normType.toUpperCase() : '';

  if (!normType || upType === 'RAG') {
    const kbItem = ITEMS.find(it => it.title === 'KB Question & Answer');
    if (kbItem) {
      return { mode: 'rag', title: kbItem.title, agentId: kbItem.agentId };
    }
    return { mode: 'rag', title: 'KB Question & Answer', agentId: null };
  }

  const byTitle = ITEMS.find(it => it.title === normType);
  if (byTitle) {
    return { mode: 'rag', title: byTitle.title, agentId: null };
  }

  if (normType) {
    return { mode: 'rag', title: normType, agentId: null };
  }

  const kbItem = ITEMS.find(it => it.title === 'KB Question & Answer');
  if (kbItem) {
    return { mode: 'rag', title: kbItem.title, agentId: kbItem.agentId };
  }
  return { mode: 'rag', title: 'KB Question & Answer', agentId: null };
}

export default function AICatalogPanel({ open, onClose, onSelect }) {
  const controlled = typeof open === 'boolean';
  const [visible, setVisible] = useState(false);
  const isOpen = controlled ? open : visible;

  const [query, setQuery] = useState('');
  const [filtered, setFiltered] = useState(ITEMS);

  const lastActiveEl = useRef(null);

  const close = useCallback(() => {
    if (!controlled) setVisible(false);
    onClose?.();
  }, [controlled, onClose]);

  const handleSelect = useCallback(
    (it) => {
      if (it.path === '/deep-research') {
        // window.open('/deep-research', '_blank', 'noopener,noreferrer');
        window.alert('This page is under preparation.');
        close();
        return;
      }

      const mode = 'rag';
      const iconSrc = ICON_MAP[it.path] || fallbackIcon;
      const detail = { ...it, mode, iconSrc };
      window.dispatchEvent(new CustomEvent('ai:select', { detail }));
      onSelect?.(detail);
      close();
    },
    [onSelect, close]
  );

  useEffect(() => {
    const onOpen = () => {
      if (!controlled) {
        lastActiveEl.current = document.activeElement;
        setVisible(true);
      }
    };
    window.addEventListener('ui:open-agent-picker', onOpen);
    return () => window.removeEventListener('ui:open-agent-picker', onOpen);
  }, [controlled]);

  useEffect(() => {
    if (!isOpen) return;
    const body = document.body;
    const count = Number(body.dataset.modalOpenCount || '0') + 1;
    body.dataset.modalOpenCount = String(count);
    body.classList.add('modal-dim');
    const onKey = (e) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      if (lastActiveEl.current && lastActiveEl.current.focus) lastActiveEl.current.focus();
      const next = Number(body.dataset.modalOpenCount || '1') - 1;
      if (next <= 0) {
        delete body.dataset.modalOpenCount;
        body.classList.remove('modal-dim');
      } else {
        body.dataset.modalOpenCount = String(next);
      }
    };
  }, [isOpen, close]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const q = query.trim().toLowerCase();
      if (!q) {
        setFiltered(ITEMS);
        return;
      }
      setFiltered(
        ITEMS.filter(
          it =>
            it.title.toLowerCase().includes(q) ||
            it.desc.toLowerCase().includes(q)
        )
      );
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);

  if (!isOpen) return null;

  const modal = (
    <div className="ai-modal-backdrop" onClick={close}>
      <div
        className="ai-modal"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 920 }}
      >
        <div className="ai-modal-header">
          <div className="ai-modal-title">Choose Your Agent</div>
          <button className="ai-modal-close" onClick={close} aria-label="Close">
            ×
          </button>
        </div>
        <div className="ai-modal-body">
          <div className="assistant-catalog">
            <div className="ai-top-row">
              <input
                type="text"
                className="ai-search-input"
                placeholder="Search agents..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>

            <ul className="list" style={{ maxHeight: 6 * 90, overflowY: 'auto' }}>
              {filtered.map((it) => {
                const Icon = ICON_MAP[it.path] || fallbackIcon;
                return (
                  <li
                    key={it.path}
                    className="list-item"
                    onClick={() => handleSelect(it)}
                    title={it.title}
                  >
                    <div className="row">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
                        <img src={Icon} alt="" width={24} height={24} />
                        <div className="col" style={{ flex: 1, minHeight: 40 }}>
                          <div className="title">{it.title}</div>
                          <div className="muted ai-desc line-clamp-2">{it.desc}</div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <div className="badge">AI</div>
                      </div>
                    </div>
                  </li>
                );
              })}
              {query && filtered.length === 0 && (
                <li className="list-item muted" style={{ textAlign: 'center' }}>
                  No results found
                </li>
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
