from fastapi import APIRouter
from app.services.features import chat_service as service

router = APIRouter(prefix='/chat', tags=['chat'])

router.post('/sessions')(service.create_session)
router.get('/sessions')(service.list_sessions)
router.get('/sessions/{session_id}')(service.get_session)
router.delete('/sessions/{session_id}')(service.delete_session)
router.post('/sessions/{session_id}/messages')(service.add_message)
router.get('/sessions/{session_id}/messages')(service.list_messages)
router.post('/feedback')(service.add_feedback)
router.get('/feedback')(service.list_feedback)
router.get('/feedback/stats')(service.feedback_stats)
router.get('/messages/search')(service.search_messages)

__all__ = ['router']
