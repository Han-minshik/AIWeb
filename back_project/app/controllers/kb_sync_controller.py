from fastapi import APIRouter
from app.services.features import kb_sync_service as service

router = APIRouter(prefix='/kb/sync', tags=['kb-sync'])

router.get('/preview')(service.preview)
router.post('/apply')(service.apply_selected)

__all__ = ['router']
