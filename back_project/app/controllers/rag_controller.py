from fastapi import APIRouter
from app.services.features import rag_service as service

router = APIRouter(prefix='/rag', tags=['rag'])

router.post('/ingest')(service.ingest_docs)
router.post('/query')(service.query_docs)

__all__ = ['router']
