import React, { useState, useEffect } from 'react';
import ReactDom from 'react-dom';
import './GraphFilterModal.css';

export default function GraphFilterModal({
    category, 
    setCategory, 
    docId, 
    setDocId, 
    onClose,
    onApply,
}) {
    const [localCategory, setLocalCategory] = useState(category || '');
    const [localDocId, setLocalDocId] = useState(docId || '');

    useEffect(() => {
        setLocalCategory(category || '');
        setLocalDocId(docId || '');
    }, [category, docId]);

    useEffect(() => {
        function handleKeyDown(e) {
            if (e.key === 'Escape') {
                if (onClose) {
                    onClose();
                }
            }
        }
        window.addEventListener('keydown', handleKeyDown);
        return () => {
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [onClose]);

    if (typeof document === 'undefined') {
        return null;
    }

    const handleOverlayClick = (e) => {
        if (e.target === e.currentTarget && onClose) {
            onClose();
        }
    };

    const handleApply = () => {
        const nextCategory = (localCategory || '').trim();
        const nextDocId = (localDocId || '').trim();
        if (setCategory) {
            setCategory(nextCategory);
        }
        if (setDocId) {
            setDocId(nextDocId);
        } 
        if (onApply) {
            onApply({ category: nextCategory, docId: nextDocId });
        } 
        if (onClose) {
            onClose();
        }
    };

    return ReactDom.createPortal(
        <div className='graph-filter-modal-overlay' onClick={handleOverlayClick}>
            <div className='graph-filter-modal' onClick={(e) => e.stopPropagation()}>
                <div className='graph-filter-modal-header'>
                    <div className='graph-filter-modal-title'>Filter Settings</div> 
                    <button className='graph-button X' type='button' onClick={onClose}>
                        X
                    </button>
                </div>
                <div className='graph-filter-modal-body'>
                    <div className='graph-filter-modal-row'>
                        <label className='graph-label'>Category</label>
                        <input
                        className='graph-input'
                        placeholder='ex) std'
                        value={localCategory}
                        onChange={(e) => setLocalCategory(e.target.value)}
                        />
                    </div>
                    <div className='graph-filter-modal-row'>
                        <label className='graph-label'>Document ID</label>  
                        <input
                        className='graph-input'
                        placeholder='ex) 2628'
                        value={localDocId}
                        onChange={(e) => setLocalDocId(e.target.value)}
                        />
                    </div>
                </div>
                <div className='graph-filter-modal-footer'>
                    <button className='graph-button' type='button' onClick={onClose}>
                        Cancel  
                    </button>
                    <button
                    className='graph-button primary'
                    type='button'
                    onClick={handleApply}
                    >
                        Apply
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}