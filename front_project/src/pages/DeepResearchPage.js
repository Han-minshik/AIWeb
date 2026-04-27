import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertCircle,
  BookOpen,
  Check,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  Globe,
  Layers2,
  ListChecks,
  Loader2,
  Moon,
  Play,
  Search,
  Sparkles,
  Square,
  Sun,
  Zap,
} from 'lucide-react';
import { streamDeepResearch } from '../services/deepResearch/deepResearchService';
import { INITIAL_PHASES, phaseStatusIcon, phaseRowClass } from '../components/deepResearch/Pipeline';
import SourceCard from '../components/deepResearch/SourceCard';
import CustomSelect from '../components/deepResearch/CustomSelect';
import gearIcon from '../assets/icons/gear.svg';
import '../styles/pages/deepResearch.css';

const DEPTH_PRESETS = {
  quick: { label: 'Quick', depth: 2, icon: Zap, desc: 'Fast sweep - essential insights only' },
  normal: { label: 'Balanced', depth: 3, icon: Search, desc: 'Thorough coverage across key areas' },
  deep: { label: 'Deep', depth: 5, icon: Sparkles, desc: 'Exhaustive research from every angle' },
};

const AI_MODELS = ['qwen3', 'claude'];

export default function DeepResearchPage() {
  const [question, setQuestion] = useState('');
  const [depthPreset, setDepthPreset] = useState('normal');
  const [maxSources, setMaxSources] = useState(15);
  const [selectedModel, setSelectedModel] = useState(() => {
    try {
      const saved = localStorage.getItem('deep_research_model');
      return AI_MODELS.includes(saved) ? saved : 'qwen3';
    } catch {
      return 'qwen3';
    }
  });

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [settingsAnchor, setSettingsAnchor] = useState(null);
  const [settingsPos, setSettingsPos] = useState({ top: 56, left: 8 });
  const [settingsMenuRect, setSettingsMenuRect] = useState(null);

  const [isRunning, setIsRunning] = useState(false);
  const [phases, setPhases] = useState(INITIAL_PHASES);
  const [brief, setBrief] = useState('');
  const [topics, setTopics] = useState([]);
  const [sources, setSources] = useState([]);
  const [report, setReport] = useState('');
  const [finalSources, setFinalSources] = useState([]);
  const [errorMsg, setErrorMsg] = useState('');
  const [doneStatus, setDoneStatus] = useState('');
  const [reportLight, setReportLight] = useState(false);
  const [showThemeHint, setShowThemeHint] = useState(false);
  const [queryExpanded, setQueryExpanded] = useState(false);

  const closeRef = useRef(null);
  const reportEndRef = useRef(null);
  const hintShownRef = useRef(false);
  const textareaRef = useRef(null);
  const queryContainerRef = useRef(null);
  const settingsBtnRef = useRef(null);
  const settingsMenuRef = useRef(null);
  const modelMenuRef = useRef(null);

  useEffect(() => () => closeRef.current?.(), []);

  useEffect(() => {
    if (!queryExpanded) return undefined;
    function onOutside(e) {
      if (queryContainerRef.current && !queryContainerRef.current.contains(e.target)) {
        setQueryExpanded(false);
      }
    }
    document.addEventListener('mousedown', onOutside);
    return () => document.removeEventListener('mousedown', onOutside);
  }, [queryExpanded]);

  useEffect(() => {
    if (isRunning && reportEndRef.current) {
      reportEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [report, isRunning]);

  useEffect(() => {
    if (report.length > 0 && !hintShownRef.current) {
      hintShownRef.current = true;
      setShowThemeHint(true);
      const t = setTimeout(() => setShowThemeHint(false), 5000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [report]);

  const updatePhase = useCallback((phaseId, patch) => {
    setPhases((prev) => prev.map((p) => (p.id === phaseId ? { ...p, ...patch } : p)));
  }, []);

  const computeSettingsPos = useCallback(() => {
    const rect = settingsAnchor;
    const gap = 8;
    const vw = typeof window !== 'undefined' ? window.innerWidth : 0;
    const vh = typeof window !== 'undefined' ? window.innerHeight : 0;
    const approxW = 220;
    const approxH = 80;

    let top = rect ? Math.round(rect.bottom + gap) : 56;
    let left = rect ? Math.round(rect.left) : 8;

    if (left + approxW > vw - 8) left = Math.max(8, vw - approxW - 8);
    if (top + approxH > vh - 8) top = Math.max(8, vh - approxH - 8);
    return { top, left };
  }, [settingsAnchor]);

  useEffect(() => {
    if (!settingsOpen) return undefined;
    const update = () => setSettingsPos(computeSettingsPos());
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [settingsOpen, computeSettingsPos]);

  useEffect(() => {
    if (!settingsOpen) {
      setModelMenuOpen(false);
      return undefined;
    }
    const id = requestAnimationFrame(() => {
      const rect = settingsMenuRef.current?.getBoundingClientRect?.();
      if (rect) setSettingsMenuRect(rect);
    });
    return () => cancelAnimationFrame(id);
  }, [settingsOpen, settingsPos]);

  useEffect(() => {
    if (!settingsOpen && !modelMenuOpen) return undefined;
    const onEsc = (e) => {
      if (e.key === 'Escape') {
        setModelMenuOpen(false);
        setSettingsOpen(false);
      }
    };
    const onDocClick = (e) => {
      const t = e.target;
      if (settingsBtnRef.current?.contains(t)) return;
      if (settingsMenuRef.current?.contains(t)) return;
      if (modelMenuRef.current?.contains(t)) return;
      setModelMenuOpen(false);
      setSettingsOpen(false);
    };
    document.addEventListener('keydown', onEsc, true);
    document.addEventListener('mousedown', onDocClick, true);
    return () => {
      document.removeEventListener('keydown', onEsc, true);
      document.removeEventListener('mousedown', onDocClick, true);
    };
  }, [settingsOpen, modelMenuOpen]);

  const resetState = useCallback(() => {
    setPhases(INITIAL_PHASES);
    setBrief('');
    setTopics([]);
    setSources([]);
    setReport('');
    setFinalSources([]);
    setErrorMsg('');
    setDoneStatus('');
    setShowThemeHint(false);
    hintShownRef.current = false;
  }, []);

  const onOpenSettingsMenu = useCallback((e) => {
    const rect = e?.currentTarget?.getBoundingClientRect?.() || null;
    setSettingsAnchor(rect);
    setSettingsOpen((v) => !v);
    setModelMenuOpen(false);
  }, []);

  const applyModel = useCallback((nextModel) => {
    if (!AI_MODELS.includes(nextModel)) return;
    setSelectedModel(nextModel);
    try {
      localStorage.setItem('deep_research_model', nextModel);
    } catch {}
    setModelMenuOpen(false);
    setSettingsOpen(false);
  }, []);

  const handleRun = useCallback(() => {
    if (!question.trim() || isRunning) return;
    resetState();
    setIsRunning(true);

    const cancel = streamDeepResearch(
      question.trim(),
      DEPTH_PRESETS[depthPreset].depth,
      maxSources,
      selectedModel,
      {
        onStatus: (phase, message) => {
          setPhases((prev) => {
            const idx = prev.findIndex((p) => p.id === phase);
            return prev.map((p, i) => {
              if (i < idx && p.status === 'active') return { ...p, status: 'done' };
              if (p.id === phase) return { ...p, status: 'active', message };
              return p;
            });
          });
          if (phase === 'error') {
            updatePhase('reporting', { status: 'error', message });
            setErrorMsg(message || 'Unknown error');
          }
        },
        onBriefToken: (text) => setBrief((prev) => prev + (text || '')),
        onBrief: (text) => {
          setBrief(text || '');
          updatePhase('brief', { status: 'done', message: 'Done' });
        },
        onTopics: (t) => {
          const items = Array.isArray(t) ? t : [];
          setTopics(items);
          updatePhase('planning', { status: 'done', message: `${items.length} topics` });
        },
        onSearchResult: (result) => setSources((prev) => [...prev, result]),
        onCompression: () => updatePhase('compressing', { status: 'done', message: 'Done' }),
        onToken: (text) => setReport((prev) => prev + (text || '')),
        onSources: (s) => setFinalSources(Array.isArray(s) ? s : []),
        onDone: (status) => {
          setPhases((prev) => prev.map((p) => (p.status === 'active' ? { ...p, status: 'done' } : p)));
          setDoneStatus(status || 'completed');
          setIsRunning(false);
          closeRef.current = null;
        },
        onError: (error) => {
          setErrorMsg(error || 'Connection error');
          setIsRunning(false);
          closeRef.current = null;
        },
      }
    );

    closeRef.current = cancel;
  }, [question, depthPreset, maxSources, selectedModel, isRunning, resetState, updatePhase]);

  const handleStop = useCallback(() => {
    closeRef.current?.();
    setIsRunning(false);
    closeRef.current = null;
    setPhases((prev) => prev.map((p) => (p.status === 'active' ? { ...p, status: 'done' } : p)));
  }, []);

  const displaySources = finalSources.length > 0 ? finalSources : sources;

  return (
    <div className="dr-page">
      <header className="dr-header">
        <div className="dr-brand">
          <div className="dr-brand-icon">
            <Search size={16} />
          </div>
          <div className="dr-brand-text">
            <span className="dr-brand-space">SPACE AI</span>
            <span className="dr-brand-divider">|</span>
            <span className="dr-brand-sub">Deep Research</span>
          </div>
        </div>
        <div className="dr-header-actions">
          <button
            type="button"
            className="dr-header-btn"
            aria-label="Settings"
            title="Settings"
            onClick={onOpenSettingsMenu}
            ref={settingsBtnRef}
          >
            <img src={gearIcon} alt="Settings" />
          </button>
        </div>
      </header>

      <section className="dr-query">
        <div className="dr-query-left">
          <div className="dr-depth-preset-wrap">
            {Object.entries(DEPTH_PRESETS).map(([key, preset], idx) => {
              const Icon = preset.icon;
              const active = depthPreset === key;
              const isFirst = idx === 0;
              return (
                <div key={key} className="dr-depth-item">
                  <button
                    type="button"
                    onClick={() => setDepthPreset(key)}
                    disabled={isRunning}
                    className={`dr-depth-btn ${active ? 'active' : ''}`}
                  >
                    <Icon size={12} />
                    {preset.label}
                  </button>
                  <div className={`dr-depth-tooltip ${isFirst ? 'first' : ''}`}>
                    <div className="dr-depth-tooltip-arrow" />
                    <p className="dr-depth-tooltip-title">{preset.label}</p>
                    <p className="dr-depth-tooltip-desc">{preset.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="dr-query-sep" />

          <div className="dr-query-control">
            <Database size={13} />
            <span>Sources</span>
            <CustomSelect
              value={maxSources}
              onChange={setMaxSources}
              options={[10, 15, 20, 30, 40, 50]}
              disabled={isRunning}
            />
          </div>
        </div>

        <div className="dr-query-sep" />

        <div className="dr-query-input-wrap">
          <Search size={16} className="dr-query-search-icon" />
          <div ref={queryContainerRef} className="dr-query-input-area">
            {queryExpanded ? (
              <div className="dr-query-expanded">
                <div className="dr-query-expanded-arrow" />
                <textarea
                  ref={textareaRef}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={4}
                  disabled={isRunning}
                  placeholder={'What do you want to research?\n(Shift+Enter for new line ， Enter to search)'}
                  className="dr-query-textarea"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      setQueryExpanded(false);
                      handleRun();
                    }
                    if (e.key === 'Escape') setQueryExpanded(false);
                  }}
                />
                <div className="dr-query-expand-foot">
                  <span>Shift+Enter ， new line ， Enter ， search</span>
                  {question.split('\n').length > 1 ? <span>{question.split('\n').length} lines</span> : null}
                </div>
              </div>
            ) : null}

            <input
              type="text"
              value={question.replace(/\n/g, ' ? ')}
              readOnly
              placeholder="What do you want to research?"
              disabled={isRunning}
              className="dr-query-input"
              onFocus={() => {
                setQueryExpanded(true);
                requestAnimationFrame(() => textareaRef.current?.focus());
              }}
            />
          </div>
        </div>

        {question.includes('\n') ? (
          <span className="dr-line-badge">{question.split('\n').length}L</span>
        ) : null}

        <div className="dr-query-sep" />

        <div className="dr-query-right">
          {isRunning ? (
            <button className="dr-btn dr-btn-stop" onClick={handleStop}>
              <Square size={12} />
              Stop
            </button>
          ) : (
            <button className="dr-btn dr-btn-run" onClick={handleRun} disabled={!question.trim()}>
              <Play size={12} />
              Search
            </button>
          )}
        </div>
      </section>

      <section className="dr-grid">
        <aside className="dr-col dr-left">
          <div className="dr-card dr-workflow-card">
            <h3 className="dr-card-title">
              <Layers2 size={16} />
              Research Workflow
            </h3>
            <div className="dr-phase-list">
              {phases.map((phase) => (
                <div key={phase.id} className={`dr-phase-row ${phaseRowClass(phase.status)}`}>
                  {phaseStatusIcon(phase.status)}
                  <div className="dr-phase-body">
                    <p className="dr-phase-label">{phase.label}</p>
                    {phase.message ? <p className="dr-phase-message">{phase.message}</p> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="dr-card dr-flex">
            <h3 className="dr-card-title">
              <Database size={16} />
              Reference Library
              {displaySources.length > 0 ? <span className="dr-badge">{displaySources.length}</span> : null}
            </h3>
            <div className="dr-source-list">
              {displaySources.length > 0 ? (
                displaySources.map((src, i) => <SourceCard key={`${src?.url || 'source'}-${i}`} source={src} />)
              ) : (
                <p className="dr-muted">Sources will appear here.</p>
              )}
            </div>
          </div>
        </aside>

        <main className="dr-col dr-center">
          <div className="dr-card dr-flex dr-report-card">
            <div className="dr-report-head">
              <h3 className="dr-card-title">
                <FileText size={16} />
                Report
              </h3>
              <div className="dr-report-head-right">
                {doneStatus === 'completed' ? (
                  <span className="dr-status done">
                    <CheckCircle2 size={12} />
                    Complete
                  </span>
                ) : null}
                {isRunning ? (
                  <span className="dr-status running">
                    <Loader2 size={12} className="spin" />
                    Generating...
                  </span>
                ) : null}
                <div className="dr-theme-wrap">
                  <div className={`dr-theme-hint ${showThemeHint ? 'show' : ''}`}>
                    <div className="dr-theme-hint-inner">
                      <Sparkles size={14} />
                      <span>
                        Switch report to <b>light</b> or <b>dark</b> view
                      </span>
                    </div>
                    <div className="dr-theme-hint-arrow" />
                  </div>
                  <div className="dr-theme-btn-wrap">
                    <button className="dr-theme-btn" onClick={() => setReportLight((v) => !v)}>
                      {reportLight ? <Sun size={14} /> : <Moon size={14} />}
                    </button>
                    <div className="dr-theme-tooltip">
                      <div className="dr-theme-tooltip-arrow" />
                      <p className="dr-theme-tooltip-title">{reportLight ? 'Light' : 'Dark'}</p>
                      <p className="dr-theme-tooltip-desc">
                        {reportLight ? 'Switch to dark view' : 'Switch to light view'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className={`dr-report-body ${reportLight ? 'light' : ''}`}>
              {report ? (
                <div className={`dr-markdown ${reportLight ? 'light' : ''}`}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {report}
                  </ReactMarkdown>
                  {isRunning ? <span className="dr-cursor" /> : null}
                  <div ref={reportEndRef} />
                </div>
              ) : errorMsg ? (
                <div className="dr-error">
                  <AlertCircle size={16} />
                  <p>{errorMsg}</p>
                </div>
              ) : (
                <div className="dr-empty">
                  <Globe size={40} />
                  <p>Ask a question to begin</p>
                </div>
              )}
            </div>
          </div>
        </main>

        <aside className="dr-col dr-right">
          <div className="dr-card dr-flex dr-brief-card">
            <h3 className="dr-card-title">
              <ListChecks size={16} />
              Research Brief
            </h3>
            <div className="dr-text-block">
              {brief ? <p>{brief.trimStart()}</p> : <p className="dr-muted">Brief will appear once research begins.</p>}
            </div>
          </div>

          {isRunning || doneStatus ? (
            <div className="dr-card dr-flex short">
              <h3 className="dr-card-title">
                <BookOpen size={16} />
                Research Topics
                {topics.length > 0 ? <span className="dr-badge">{topics.length}</span> : null}
              </h3>
              <div className="dr-topic-list">
                {topics.length > 0 ? (
                  topics.map((t) => (
                    <div key={t.id} className="dr-topic-item">
                      <p className="dr-topic-title">{t.aspect}</p>
                      <p className="dr-topic-query">{t.query}</p>
                    </div>
                  ))
                ) : (
                  <p className="dr-muted">Planning topics...</p>
                )}
              </div>
            </div>
          ) : null}

          {isRunning || doneStatus ? (
            <div className="dr-card dr-stats-card">
              <h3 className="dr-card-title">
                <Clock3 size={16} />
                Session Stats
              </h3>
              <div className="dr-stats">
                <div className="dr-stat-box">
                  <p>Topics</p>
                  <b>{topics.length}</b>
                </div>
                <div className="dr-stat-box">
                  <p>Sources</p>
                  <b>{displaySources.length}</b>
                </div>
              </div>
            </div>
          ) : null}
        </aside>
      </section>

      {settingsOpen &&
        createPortal(
          <div
            className="dropdown dr-settings-menu"
            ref={settingsMenuRef}
            role="menu"
            aria-label="Deep Research Settings Menu"
            style={{
              position: 'fixed',
              top: settingsPos.top,
              left: settingsPos.left,
              zIndex: 2147483647,
              minWidth: 220,
            }}
          >
            <button
              className="dropdown_item dr-dropdown-trigger"
              role="menuitem"
              onClick={() => setModelMenuOpen((v) => !v)}
            >
              <span>AI Model</span>
              <span className="dr-dropdown-meta">{selectedModel}</span>
            </button>
          </div>,
          document.body
        )}

      {settingsOpen && modelMenuOpen &&
        createPortal(
          <div
            className="dropdown dr-model-menu"
            ref={modelMenuRef}
            role="menu"
            aria-label="AI Model Menu"
            style={{
              position: 'fixed',
              top: settingsMenuRect ? Math.round(settingsMenuRect.top) : settingsPos.top,
              left: settingsMenuRect
                ? Math.max(8, Math.round(settingsMenuRect.left) - 220 - 8)
                : Math.max(8, settingsPos.left - 220 - 8),
              zIndex: 2147483647,
              minWidth: 220,
            }}
          >
            {AI_MODELS.map((modelName) => (
              <button
                key={modelName}
                className={`dropdown_item dr-model-option ${selectedModel === modelName ? 'active' : ''}`}
                role="menuitemradio"
                aria-checked={selectedModel === modelName}
                onClick={() => applyModel(modelName)}
              >
                <span>{modelName}</span>
                {selectedModel === modelName ? <Check size={14} className="dr-model-check" /> : null}
              </button>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
}
