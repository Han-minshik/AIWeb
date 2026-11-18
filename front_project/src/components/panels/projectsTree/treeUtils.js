import pdfIcon from '../../../assets/icons/colorSquarePDF.svg';
import hwpIcon from '../../../assets/icons/hwp.svg';
import docxIcon from '../../../assets/icons/docx.svg';
import xlsIcon from '../../../assets/icons/xls.svg';
import htmlIcon from '../../../assets/icons/html.svg';
import textFileIcon from '../../../assets/icons/textFile.svg';
import exclamationIcon from '../../../assets/icons/exclamation.svg';

export const CONTEXT_MENU_W = 200;
export const CONTEXT_MENU_H = 240;
export const CONTEXT_MENU_MARGIN = 8;

export const normalizeFolderKey = (raw) =>
  String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/^\s*\d+\s*[\.\)\-:]*\s*/, '');

export const FOLDER_INDEXING_STATE_BY_PATH = Object.freeze({});

export const INDEXING_STATE_META = Object.freeze({
  preparing: {
    icon: exclamationIcon,
    tooltip: 'This folder is being prepared for indexing.',
    className: 'is-preparing',
  },
});

export const getFileExt = (node) => {
  const extFromMeta = String(
    node?.meta?.storage_object_file_right || node?.meta?.file_right || node?.meta?.fileRight || ''
  )
    .trim()
    .replace(/^\./, '')
    .toLowerCase();
  if (extFromMeta) return extFromMeta;

  const name = String(node?.name || '').trim();
  const m = name.match(/\.([A-Za-z0-9]+)$/);
  if (m?.[1]) return String(m[1]).toLowerCase();

  const mime = String(node?.meta?.mime_type || node?.meta?.mimeType || '').toLowerCase();
  if (!mime) return '';
  if (mime.includes('pdf')) return 'pdf';
  if (mime.includes('hwp')) return 'hwp';
  if (mime.includes('msword') || mime.includes('wordprocessingml')) return 'docx';
  if (mime.includes('excel') || mime.includes('spreadsheetml')) return 'xls';
  return '';
};

export const getFileIcon = (node) => {
  const ext = getFileExt(node);
  if (ext === 'pdf') return pdfIcon;
  if (ext === 'hwp' || ext === 'hwpx') return hwpIcon;
  if (ext === 'doc' || ext === 'docx') return docxIcon;
  if (ext === 'xls' || ext === 'xlsx') return xlsIcon;
  if (ext === 'md') return htmlIcon;
  return textFileIcon;
};

export const usesGenericTextFileIcon = (node) => getFileIcon(node) === textFileIcon;

export function toId(v) {
  return Number(v);
}

export function sameId(a, b) {
  return Number(a) === Number(b);
}

export function getContextMenuPos(e) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let x = e.clientX;
  let y = e.clientY;

  if (x + CONTEXT_MENU_W + CONTEXT_MENU_MARGIN > vw) {
    x = vw - CONTEXT_MENU_W - CONTEXT_MENU_MARGIN;
  }
  if (y + CONTEXT_MENU_H + CONTEXT_MENU_MARGIN > vh) {
    y = vh - CONTEXT_MENU_H - CONTEXT_MENU_MARGIN;
  }

  if (x < CONTEXT_MENU_MARGIN) x = CONTEXT_MENU_MARGIN;
  if (y < CONTEXT_MENU_MARGIN) y = CONTEXT_MENU_MARGIN;

  return { x, y };
}

export function normalizeNode(node) {
  const getId = (n) => n?.id ?? n?.node_id;
  const getName = (n) => n?.name ?? n?.title ?? `Node ${getId(n)}`;
  const getChildren = (n) => n?.children || [];
  const docStatus = String(node?.doc_status ?? node?.docStatus ?? node?.meta?.doc_status ?? node?.meta?.docStatus ?? '')
    .trim()
    .toLowerCase();
  const disabled = Boolean(node?.disabled) || ['deleted_pending', 'inactive_path_missing'].includes(docStatus);

  const isFolder = (n) => {
    if (typeof n?.is_folder === 'boolean') return n.is_folder;
    const t = (n?.node_type || n?.kind || '').toString().toLowerCase();
    if (t.includes('folder')) return true;
    if (t.includes('file')) return false;
    return !!(n?.children && n.children.length);
  };

  return {
    id: getId(node),
    name: getName(node),
    node_type: node?.node_type ?? node?.kind,
    is_folder: isFolder(node),
    parent: node?.parent ?? node?.parent_id ?? null,
    disabled,
    doc_status: docStatus,
    meta: {
      ...(node?.meta || {}),
      doc_status: docStatus || node?.meta?.doc_status,
    },
    children: (getChildren(node) || []).map(normalizeNode),
  };
}

export function buildLookupFilename(baseName, ext) {
  const base = String(baseName || '').trim();
  if (!base) return '';
  const right = String(ext || '').trim().replace(/^\./, '');
  if (!right) return base;
  const lowerBase = base.toLowerCase();
  const lowerExt = right.toLowerCase();
  if (lowerBase.endsWith(`.${lowerExt}`)) return base;
  return `${base}.${right}`;
}

export function inferBranchTypeFromPathParts(parts = []) {
  const normalized = (parts || [])
    .map((p) => String(p || '').trim().toLowerCase())
    .map((p) => p.replace(/^\s*\d+\s*[\.\)\-:]*\s*/, ''));
  if (normalized.some((p) => p === 'kiwix' || p.includes('kiwix'))) return 'KIWIX';
  return null;
}

export function toSafeTitle(title, docId) {
  const t = String(title || '').trim();
  if (t) return t;
  if (docId != null) return `Doc ${docId}`;
  return 'Doc';
}
