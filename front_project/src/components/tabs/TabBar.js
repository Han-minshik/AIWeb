import React, { useEffect, useRef } from 'react';
import PlusIcon from '../../assets/icons/plus.svg';
import XIcon from '../../assets/icons/x.svg';

const TAB_MIN_WIDTH = 110;
const TAB_MAX_WIDTH = 200;

export default function TabBar({
  tabs = [],
  activeId,
  onSelect,
  onClose,
  onNew,
  onDragTabStart,
  onDragTabEnd,
}) {
  const activeRef = useRef(null);

  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'center',
      });
    }
  }, [activeId, tabs.length]);

  return (
    <div className="tabs embed tabs-modern">
      <div className="tabs-scroll" role="tablist" aria-label="Tabs">
        {tabs.map((t, index) => {
          const isActive = t.id === activeId;

          return (
            <button
              key={t.id}
              ref={isActive ? activeRef : null}
              className={`tab-item ${isActive ? 'is-active' : ''}`}
              role="tab"
              aria-selected={isActive}
              aria-posinset={index + 1}
              aria-setsize={tabs.length}
              title={t.title}
              type="button"
              onClick={() => onSelect?.(t.id)}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', String(t.id));
                e.dataTransfer.effectAllowed = 'move';
                onDragTabStart?.(t.id);
                e.stopPropagation();
              }}
              onDragEnd={() => onDragTabEnd?.()}
              style={{
                minWidth: TAB_MIN_WIDTH,
                maxWidth: TAB_MAX_WIDTH,
              }}
            >
              <span className="tab-item-inner">
                <span className="tab-title" data-active={isActive ? 'true' : 'false'}>
                  {t.title}
                </span>

                <span className="tab-actions">
                  <span
                    className="tab-icon tab-icon-close"
                    role="button"
                    tabIndex={0}
                    aria-label="close-tab"
                    title="Close"
                    onClick={(e) => {
                      e.stopPropagation();
                      onClose?.(t.id);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        e.stopPropagation();
                        onClose?.(t.id);
                      }
                    }}
                  >
                    <img src={XIcon} alt="close" />
                  </span>
                </span>
              </span>
            </button>
          );
        })}

        {onNew && (
          <button
            className="tab-item tab-plus"
            title="New Tab"
            aria-label="new-tab"
            type="button"
            onClick={() => onNew?.()}
          >
            <span className="tab-item-inner tab-plus-inner">
              <img src={PlusIcon} alt="new tab" />
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
