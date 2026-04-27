import React from 'react';
import useDownload from '../../hooks/useDownload';

export default function WordViewer({ docId }) {
  const { downloadLatest } = useDownload();
  return (
    <div className="viewer generic">
      <p>Word Preview is replaced by Download.</p>
      <button className="btn" onClick={() => downloadLatest(docId)}>Download</button>
    </div>
  );
}
