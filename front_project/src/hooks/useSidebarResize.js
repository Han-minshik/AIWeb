import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  LEFT_COLLAPSED,
  LEFT_MIN,
  LEFT_MAX,
  RIGHT_MIN,
  RIGHT_MAX,
} from '../utils/constants';

export default function useSidebarResize() {
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [leftWidth, setLeftWidth] = useState(320);
  const [rightWidth, setRightWidth] = useState(650);
  const [isResizing, setIsResizing] = useState(false);
  const [rightUserSized, setRightUserSized] = useState(false);

  const dragRef = useRef(null);

  const clamp = useCallback((v, lo, hi) => Math.max(lo, Math.min(hi, v)), []);

  const stateRef = useRef({
    leftOpen: true,
    rightOpen: true,
    leftWidth: 320,
    rightWidth: 520,
    rightUserSized: false,
  });

  useEffect(() => {
    stateRef.current = {
      leftOpen,
      rightOpen,
      leftWidth,
      rightWidth,
      rightUserSized,
    };
  }, [leftOpen, rightOpen, leftWidth, rightWidth, rightUserSized]);

  const stopDrag = useCallback(() => {
    dragRef.current = null;
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', stopDrag);
    setIsResizing(false);
  }, []);

  const onDrag = useCallback(
    (e) => {
      const s = dragRef.current;
      if (!s) return;

      const vw = window.innerWidth;
      const st = stateRef.current;

      if (s.side === 'left') {
        const dx = e.clientX - s.startX;
        const tentative = s.startWidth + dx;

        if (tentative <= LEFT_COLLAPSED + 8) {
          setLeftOpen(false);
          return;
        }

        const maxByViewport = vw - (st.rightOpen ? st.rightWidth : 0);
        const clamped = clamp(tentative, LEFT_MIN, Math.min(LEFT_MAX, maxByViewport));
        setLeftOpen(true);
        setLeftWidth(clamped);
        return;
      }

      if (s.side === 'right') {
        const dx = s.startX - e.clientX;
        const tentative = s.startWidth + dx;

        if (tentative <= 8) {
          setRightOpen(false);
          return;
        }

        const maxByViewport = vw - (st.leftOpen ? st.leftWidth : LEFT_COLLAPSED);
        const clamped = clamp(tentative, RIGHT_MIN, Math.min(RIGHT_MAX, maxByViewport));
        setRightOpen(true);
        setRightWidth(clamped);
        setRightUserSized(true);
        return;
      }

      if (s.side === 'meet') {
        let x = clamp(e.clientX, st.leftOpen ? LEFT_MIN : LEFT_COLLAPSED, vw - RIGHT_MIN);
        x = Math.min(x, LEFT_MAX);
        x = Math.max(x, vw - RIGHT_MAX);

        const L = x;
        const R = vw - x;

        setLeftOpen(true);
        setRightOpen(true);
        setLeftWidth(L);
        setRightWidth(R);
        setRightUserSized(true);
      }
    },
    [clamp]
  );

  const onDragRef = useRef(onDrag);
  useEffect(() => {
    onDragRef.current = onDrag;
  }, [onDrag]);

  function onMouseMove(e) {
    onDragRef.current?.(e);
  }

  const startDrag = useCallback(
    (side, e) => {
      e.preventDefault();

      const st = stateRef.current;
      const startX = e.clientX;

      if (side === 'left') {
        const startWidth = st.leftOpen ? st.leftWidth : LEFT_MIN;
        dragRef.current = { side, startX, startWidth };
      } else if (side === 'right') {
        const startWidth = st.rightOpen ? st.rightWidth : RIGHT_MIN;
        dragRef.current = { side, startX, startWidth };
        setRightUserSized(true);
      } else {
        dragRef.current = { side, startX };
        setRightUserSized(true);
      }

      setIsResizing(true);
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', stopDrag);
    },
    [stopDrag]
  );

  const gridStyle = useMemo(() => {
    return {
      '--left-w': leftOpen ? `${leftWidth}px` : `${LEFT_COLLAPSED}px`,
      '--right-w': rightOpen ? `${rightWidth}px` : '0px',
    };
  }, [leftOpen, leftWidth, rightOpen, rightWidth]);

  const toggleLeft = useCallback(() => {
    setLeftOpen((prev) => {
      if (prev) return false;
      setLeftWidth((w) => clamp(Math.max(w, LEFT_MIN), LEFT_MIN, LEFT_MAX));
      return true;
    });
  }, [clamp]);

  const toggleRight = useCallback(() => {
    stopDrag();
    setRightOpen((prev) => {
      if (prev) return false;
      setRightWidth((w) => clamp(Math.max(w, RIGHT_MIN), RIGHT_MIN, RIGHT_MAX));
      setRightUserSized(true);
      return true;
    });
  }, [clamp, stopDrag]);

  useEffect(() => {
    const handleResize = () => {
      const vw = window.innerWidth;
      const st = stateRef.current;

      setLeftWidth((prevLeft) => {
        const maxByViewport = vw - (st.rightOpen ? st.rightWidth : 0);
        return clamp(prevLeft, LEFT_MIN, Math.min(LEFT_MAX, maxByViewport));
      });

      setRightWidth((prevRight) => {
        const maxByViewport = vw - (st.leftOpen ? st.leftWidth : LEFT_COLLAPSED);
        return clamp(prevRight, RIGHT_MIN, Math.min(RIGHT_MAX, maxByViewport));
      });
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [clamp]);

  const isMet = (() => {
    if (!leftOpen || !rightOpen) return false;
    if (!rightUserSized) return false;
    return leftWidth + rightWidth >= window.innerWidth;
  })();

  return {
    leftOpen,
    rightOpen,
    leftWidth,
    rightWidth,
    gridStyle,
    startDrag,
    toggleLeft,
    toggleRight,
    isMet,
    setLeftOpen,
    setRightOpen,
    isResizing,
  };
}
