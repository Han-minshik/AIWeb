import { downloadLatestDocBlob } from '../services/docs/docService';

export default function useDownload() {
  const downloadLatest = async (docId) => {
    const { blob, filename } = await downloadLatestDocBlob(docId);

    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  };

  return { downloadLatest };
}
