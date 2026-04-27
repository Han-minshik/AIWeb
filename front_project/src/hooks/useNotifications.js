import { useCallback, useEffect, useRef, useState } from 'react';
import { getUnreadCount } from '../services/notifications/notificationService';

export default function useNotifications(opts = {}) {
  const intervalMs = typeof opts.intervalMs === 'number' ? opts.intervalMs : 20000;
  const [unread, setUnread] = useState(0);
  const aliveRef = useRef(true);
  const userId = parseInt(localStorage.getItem('x_user_id') || '1', 10);

  const refresh = useCallback(async () => {
    try {
      const n = await getUnreadCount(userId);
      if (!aliveRef.current) return;
      setUnread(typeof n === 'number' ? n : 0);
    } catch {
    }
  }, [userId]);

  useEffect(() => {
    aliveRef.current = true;
    refresh();

    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    const onManual = () => refresh();
    window.addEventListener('notifications:refresh', onManual);
    const id = setInterval(refresh, intervalMs);

    return () => {
      aliveRef.current = false;
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('notifications:refresh', onManual);
      clearInterval(id);
    };
  }, [intervalMs, refresh]);

  return { unread, refresh };
}
