from fastapi import APIRouter
from app.services.features import kb_dashboard_service as service

router = APIRouter(prefix='/kb', tags=['kb-dashboard'])

router.get('/trees/{kb_id}/dashboard/summary')(service.kb_dashboard_summary)
router.get('/trees/{kb_id}/dashboard/growth')(service.kb_dashboard_growth)
router.get('/dashboard/stats/opensearch')(service.get_opensearch_stats)
router.get('/dashboard/stats/activity')(service.get_activity_stats)
router.get('/dashboard/stats/recent')(service.get_recent_stats)

__all__ = ['router']
