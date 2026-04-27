import { useCallback, useMemo, useRef, useState } from 'react';

export default function usePaneSplit(layout) {
    const [colSplit, setColSplit] = useState(0.7);
    const [rowSplit, setRowSplit] = useState(0.5);

    const panesStyle = useMemo(() => {
        const col1 = Math.max(0.15, Math.min(0.85, colSplit));
        const row1 = Math.max(0.15, Math.min(0.85, rowSplit));
        const hLeft = layout === '3L' ? 'var(--col-1)' : '0%';
        const hRight = layout === '3R' ? 'var(--col-1)' : '100%';
        return {
            '--col-1': `${(col1 * 100).toFixed(2)}%`,
            '--col-2': `${((1 - col1) * 100).toFixed(2)}%`,
            '--row-1': `${(row1 * 100).toFixed(2)}%`,
            '--row-2': `${((1 - row1) * 100).toFixed(2)}%`,
            '--gut-x': `${(col1 * 100).toFixed(4)}%`,
            '--gut-y': `${(row1 * 100).toFixed(4)}%`,
            '--h-left': hLeft,
            '--h-right': hRight,
        };
    }, [colSplit, rowSplit, layout]);

    const resDrag = useRef(null);
    const stopPaneResizeRef = useRef(() => { });

    const onPaneResize = useCallback((e) => {
        const r = resDrag.current;
        if (!r) return;
        const { type, rect } = r;
        if (type === 'col') {
            const px = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
            const ratio = px / rect.width;
            setColSplit(Math.max(0.15, Math.min(0.85, ratio)));
        } else {
            const py = Math.min(Math.max(e.clientY - rect.top, 0), rect.height);
            const ratio = py / rect.height;
            setRowSplit(Math.max(0.15, Math.min(0.85, ratio)));
        }
    }, []);

    stopPaneResizeRef.current = () => {
        resDrag.current = null;
        window.removeEventListener('mousemove', onPaneResize);
        window.removeEventListener('mouseup', stopPaneResizeRef.current);
    };

    const startPaneResize = useCallback((type, e) => {
        e.preventDefault();
        const container = e.currentTarget.closest('.panes');
        if (!container) return;
        const rect = container.getBoundingClientRect();
        resDrag.current = { type, rect };
        window.addEventListener('mousemove', onPaneResize);
        window.addEventListener('mouseup', stopPaneResizeRef.current);
    }, [onPaneResize]);

    const showColResizer = layout === '2' || layout === '3L' || layout === '3R' || layout === '4';
    const showRowResizer = layout === '3L' || layout === '3R' || layout === '4';

    const splitSideClass = layout === '3L' ? 'split-right' : layout === '3R' ? 'split-left' : '';

    return { panesStyle, startPaneResize, showColResizer, showRowResizer, splitSideClass };
}