const STAGE_LABELS = {
  parse: '파싱',
  index: '인덱싱',
  delete: '영구 삭제',
};

const STATUS_LABELS = {
  pending: '대기',
  running: '요청 중',
  sent: '처리 중',
  completed: '완료',
  failed: '실패',
  cancelled: '취소',
  superseded: '이전 버전',
};

export function pipelineJobLabel(row) {
  const stage = String(row?.stage || '').toLowerCase();
  const status = String(row?.status || '').toLowerCase();
  const stageLabel = STAGE_LABELS[stage] || stage || '작업';
  const statusLabel = STATUS_LABELS[status] || status || '-';
  return `${stageLabel} ${statusLabel}`;
}

export function pipelineStatusClass(status) {
  return `pipeline-status pipeline-status-${String(status || 'unknown').toLowerCase()}`;
}

export function retryableJobs(rows) {
  return (rows || []).filter((row) => row?.retry_allowed === true);
}