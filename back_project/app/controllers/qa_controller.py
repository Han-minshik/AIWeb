from fastapi import APIRouter
from app.services.features import qa_service as service

router = APIRouter(prefix='/qa', tags=['qa'])

router.get('/stream')(service.stream_get)
router.post('/stream')(service.stream_post)

__all__ = ['router']
