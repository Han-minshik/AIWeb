import React from 'react';
import useDownload from '../../hooks/useDownload';

export default function PptxViewer({ docId }) {
  const { downloadLatest } = useDownload();
  return (
    <div className="viewer generic">
      <p>PPTX preview is replaced by download due to browser limitations.</p>
      <button className="btn" onClick={() => downloadLatest(docId)}>Download</button>
    </div>
  );
}
