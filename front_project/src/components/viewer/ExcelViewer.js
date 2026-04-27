import React from 'react';
import useDownload from '../../hooks/useDownload';

export default function ExcelViewer({ docId }) {
  const { downloadLatest } = useDownload();
  return (
    <div className="viewer generic">
      <p>Excel preview is replaced by download.</p>
      <button className="btn" onClick={() => downloadLatest(docId)}>Download</button>
    </div>
  );
}
