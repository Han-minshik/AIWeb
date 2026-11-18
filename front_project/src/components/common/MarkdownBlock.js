import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeKatex from 'rehype-katex';
import DOMPurify from 'dompurify';
import 'katex/dist/katex.min.css';

export default function MarkdownBlock({ content = '', preserveLineBreaks = false, className = '' }) {
  const safe = useMemo(() => {
    const clean = DOMPurify.sanitize(String(content), {
      ALLOWED_TAGS: false, 
      ALLOWED_ATTR: false, 
      USE_PROFILES: { html: true },
      FORBID_TAGS: ['script', 'style'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover'],
    });
    return clean;
  }, [content]);
  const remarkPlugins = useMemo(
    () => (preserveLineBreaks ? [remarkGfm, remarkMath, remarkBreaks] : [remarkGfm, remarkMath]),
    [preserveLineBreaks]
  );

  return (
    <div className={`md markdown-body ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={[rehypeRaw, rehypeKatex]}
        components={{
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer">
              {props.children}
            </a>
          ),
          img: ({ node, ...props }) => (
            <img
              {...props}
              alt={props.alt || ''}
              loading="lazy"
              style={{ maxWidth: '100%', borderRadius: 8 }}
            />
          ),
          table: ({ node, ...props }) => (
            <table
              {...props}
              style={{
                borderCollapse: 'collapse',
                width: '100%',
                margin: '8px 0',
              }}
            />
          ),
          th: ({ node, ...props }) => (
            <th
              {...props}
              style={{
                border: '1px solid var(--line, #ccc)',
                background: 'var(--panel-2, #f8f8f8)',
                padding: '6px 10px',
              }}
            />
          ),
          td: ({ node, ...props }) => (
            <td
              {...props}
              style={{
                border: '1px solid var(--line, #ccc)',
                padding: '6px 10px',
              }}
            />
          ),
          code: ({ node, inline, className, children, ...props }) => (
            <code
              {...props}
              className={className}
              style={{
                background: inline ? 'var(--panel-2, #f6f6f6)' : 'var(--panel, #272822)',
                color: inline ? 'inherit' : '#f8f8f2',
                padding: inline ? '2px 4px' : '10px',
                borderRadius: 6,
                display: inline ? 'inline' : 'block',
                overflowX: 'auto',
              }}
            >
              {children}
            </code>
          ),
        }}
      >
        {safe}
      </ReactMarkdown>
    </div>
  );
}
