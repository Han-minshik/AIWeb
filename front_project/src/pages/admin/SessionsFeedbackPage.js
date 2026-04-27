import React, { useCallback, useEffect, useMemo, useState } from 'react';
import AdminGuard from '../../components/admin/AdminGuard';
import FeedbackStatsChart from '../../components/admin/FeedbackStatsChart';
import SessionFeedbackFilters from '../../components/admin/session/SessionFeedbackFilters';
import SessionFeedbackSummaryCards from '../../components/admin/session/SessionFeedbackSummaryCards';
import SessionFeedbackTable from '../../components/admin/session/SessionFeedbackTable';
import SessionFeedbackDetailPanel from '../../components/admin/session/SessionFeedbackDetailPanel';
import { getFeedbackStats, listFeedback } from '../../services/feedback/feedbackService';
import '../../styles/components/sessionFeedback.css';

export default function SessionsFeedbackPage() {
  const today = new Date();
  const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  const defEndYm = ym(today);
  const past = new Date(today);
  past.setMonth(past.getMonth() - 5);
  const defStartYm = ym(past);

  const [startYm, setStartYm] = useState(defStartYm);
  const [endYm, setEndYm] = useState(defEndYm);
  const [groupBy, setGroupBy] = useState('month');
  const [appType, setAppType] = useState('');
  const [loadingStats, setLoadingStats] = useState(false);
  const [loadingList, setLoadingList] = useState(false);

  const [stats, setStats] = useState([]);
  const [feedbackRows, setFeedbackRows] = useState([]);
  const [selectedFeedback, setSelectedFeedback] = useState(null);

  const startDate = useMemo(() => `${startYm}-01`, [startYm]);
  const endDate = useMemo(() => {
    const [y, m] = endYm.split('-').map(Number);
    const last = new Date(y, m, 0).getDate();
    return `${endYm}-${String(last).padStart(2, '0')}`;
  }, [endYm]);

  const loadStats = useCallback(async () => {
    setLoadingStats(true);
    try {
      const s = await getFeedbackStats(startDate, endDate, groupBy, appType || undefined);
      const mapped = Array.isArray(s)
        ? s.map((r) => ({
            label: r.label,
            likes: r.likes ?? r.up ?? 0,
            dislikes: r.dislikes ?? r.down ?? 0,
            total: r.total ?? 0,
          }))
        : [];
      setStats(mapped);
    } catch {
      setStats([]);
    } finally {
      setLoadingStats(false);
    }
  }, [startDate, endDate, groupBy, appType]);

  const loadRaw = useCallback(async () => {
    setLoadingList(true);
    try {
      const rows = await listFeedback(startDate, endDate, appType || undefined);
      setFeedbackRows(Array.isArray(rows) ? rows : []);
      if (rows && rows.length > 0) {
        setSelectedFeedback(rows[0]);
      } else {
        setSelectedFeedback(null);
      }
    } catch {
      setFeedbackRows([]);
      setSelectedFeedback(null);
    } finally {
      setLoadingList(false);
    }
  }, [startDate, endDate, appType]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  return (
    <AdminGuard>
      <div className="panel" style={{ height: '100%' }}>
        <div className="panel-head">
          <div className="panel-title">Session / Feedback</div>
          <SessionFeedbackFilters
            startYm={startYm}
            endYm={endYm}
            groupBy={groupBy}
            appType={appType}
            loading={loadingStats || loadingList}
            onChangeStartYm={setStartYm}
            onChangeEndYm={setEndYm}
            onChangeGroupBy={setGroupBy}
            onChangeAppType={setAppType}
            onSubmitStats={loadStats}
            onShowRaw={loadRaw}
          />
        </div>

        <div className="panel-body feedback-admin-page">
          <SessionFeedbackSummaryCards stats={stats} />
          <FeedbackStatsChart data={stats} groupBy={groupBy} />
          <div className="feedback-main">
            <SessionFeedbackTable
              rows={feedbackRows}
              onSelectRow={setSelectedFeedback}
            />
            <SessionFeedbackDetailPanel feedback={selectedFeedback} />
          </div>
        </div>
      </div>
    </AdminGuard>
  );
}
