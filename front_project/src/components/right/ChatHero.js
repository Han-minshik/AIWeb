import React from 'react';

export default function ChatHero({
  onPickQuestion,
  agentName = 'KB Question & Answer',
  exampleQuestions = [],
}) {
  const pick = (q, item, idx) => {
    if (typeof onPickQuestion === 'function') onPickQuestion(q, item, idx);
  };

  const subtitle =
    agentName === 'General QA'
      ? 'Ask any general question. I will focus on direct answers and practical guidance.'
      : 'I can help you find information within your knowledge base documents, answer questions, and summarize content.';

  return (
    <div className="hero-wrap hero-abs-center">
      <div className="rightbot" aria-hidden="true">
        <span className="rightbot__icon" />
      </div>

      <div className="assistant-hero">How can I help you?</div>
      <div className="sub">{subtitle}</div>

      {exampleQuestions.length > 0 && (
        <div className="hero-options">
          {exampleQuestions.map((item, idx) => (
            <button
              key={`${item?.title || 'example'}-${idx}`}
              type="button"
              className="option-card"
              onClick={() => pick(item?.question || '', item, idx)}
            >
              <div className="title">{item?.title || `Example ${idx + 1}`}</div>
              <div className="desc">{item?.question || ''}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
