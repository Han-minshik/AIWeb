import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getUnreadCount,
  subscribeNotifications,
} from '../services/notifications/notificationService';

export default function useNotifications(opts = {}) {
  const intervalMs = typeof opts.intervalMs === 'number' ? opts.intervalMs : 60000;
  const [unread, setUnread] = useState(0);
  const aliveRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const count = await getUnreadCount();
      if (!aliveRef.current) return;
      setUnread(typeof count === 'number' ? count : 0);
    } catch {
      // SSE reconnect and the periodic fallback will retry.
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    refresh();

    const unsubscribe = subscribeNotifications({
      onEvent: (event) => {
        if (!aliveRef.current) return;
        if (typeof event?.unread === 'number') setUnread(event.unread);
        else refresh();
        if (event?.type !== 'hello') {
          window.dispatchEvent(new CustomEvent('notifications:changed'));
        }
        if (event?.type === 'notify' && event?.source === 'pipeline') {
          const stageLabel = {
            parse: '파싱',
            index: '인덱싱',
            delete: '영구 삭제',
            artifact_delete: '파싱 결과 삭제',
          }[String(event?.stage || '').toLowerCase()] || 'AI 처리';
          const resultLabel = event?.success === false ? '실패' : '완료';
          const documentName = String(event?.document || '문서').trim() || '문서';
          window.dispatchEvent(new CustomEvent('ui:toast', {
            detail: {
              message: `${documentName} ${stageLabel} ${resultLabel}`,
              type: event?.success === false ? 'error' : 'success',
              duration: 3000,
              key: `pipeline:${event?.id || event?.job_id || ''}:${event?.stage || ''}`,
            },
          }));
        }
      },
    });
    const onFocus = () => refresh();
    const onManual = () => refresh();
    window.addEventListener('focus', onFocus);
    window.addEventListener('notifications:refresh', onManual);
    const fallbackId = setInterval(refresh, intervalMs);

    return () => {
      aliveRef.current = false;
      unsubscribe();
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('notifications:refresh', onManual);
      clearInterval(fallbackId);
    };
  }, [intervalMs, refresh]);

  return { unread, refresh };
}
