import { dispatchOpenDocTab } from '../utils/openDocTab';
import { downloadLatestDocBlob } from '../services/docs/docService';

export default function useDownload() {
  const downloadLatest = async (docId) => {
    const { blob, filename } = await downloadLatestDocBlob(docId);

    dispatchOpenDocTab({ docId, objectId: docId });

    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  return { downloadLatest };
}
