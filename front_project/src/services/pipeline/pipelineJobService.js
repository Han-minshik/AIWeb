import { http } from '../http/client';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export async function listPipelineJobs(params = {}) {
  return await http.get('/pipeline/jobs', params);
}

export async function retryPipelineJobs(jobIds = []) {
  return await http.post('/pipeline/jobs/retry', { job_ids: jobIds });
}

export function subscribePipelineJobs({ scope = 'auto', onSnapshot, onError } = {}) {
  const query = new URLSearchParams({ scope }).toString();
  const source = new EventSource(`${API_BASE}/pipeline/jobs/stream?${query}`, {
    withCredentials: true,
  });
  source.addEventListener('pipeline', (event) => {
    try {
      const payload = JSON.parse(event.data || '{}');
      onSnapshot?.(payload);
      window.dispatchEvent(new CustomEvent('notifications:refresh'));
    } catch (error) {
      onError?.(error);
    }
  });
  source.onerror = (error) => onError?.(error);
  return () => source.close();
}