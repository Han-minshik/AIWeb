import { useEffect, useMemo, useState } from 'react';
import { fetchDocAsText } from '../services/docs/docTextService';

function extractHeadings(md) {
  const lines = (md || '').split(/\r?\n/);
  const headings = [];
  for (let i = 0; i < lines.length; i++) {
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(lines[i]);
    if (m) {
      headings.push({ level: m[1].length, text: m[2], line: i + 1 });
    }
  }
  return headings;
}

function looksLikeMarkdown(text) {
  if (!text) return false;
  const headings = extractHeadings(text);
  if (headings.length === 0) return false;
  const lineCount = text.split(/\r?\n/).length || 1;
  return headings.length >= 1 && headings.length <= Math.max(200, Math.floor(lineCount * 0.5));
}

export default function useMarkdownToc(docIds, opts = {}) {
  const limit = typeof opts.limit === 'number' ? opts.limit : 10; 
  const concurrency = typeof opts.concurrency === 'number' ? opts.concurrency : 3;

  const [loading, setLoading] = useState(false);
  const [toc, setToc] = useState([]); 
  const [error, setError] = useState('');

  const list = useMemo(() => Array.from(new Set(docIds || [])).slice(0, limit), [docIds, limit]);

  useEffect(() => {
    let aborted = false;
    if (!list.length) { setToc([]); setError(''); return; }

    (async () => {
      setLoading(true);
      setError('');
      const results = [];
      try {
        let idx = 0;
        async function worker() {
          while (!aborted && idx < list.length) {
            const i = idx++;
            const id = list[i];
            const text = await fetchDocAsText(id);
            if (!aborted && text && looksLikeMarkdown(text)) {
              const headings = extractHeadings(text);
              const title = (headings.find(h => h.level === 1)?.text) || (headings[0]?.text) || `Doc ${id}`;
              results.push({ docId: id, title, headings });
            }
          }
        }
        const workers = Array.from({ length: Math.min(concurrency, list.length) }, () => worker());
        await Promise.all(workers);
        if (!aborted) setToc(results);
      } catch (e) {
        if (!aborted) {
          setError(e?.message || 'Failed to load table of contents.');
          setToc([]);
        }
      } finally {
        if (!aborted) setLoading(false);
      }
    })();

    return () => { aborted = true; };
  }, [list, concurrency]);

  return { loading, toc, error, count: list.length };
}
