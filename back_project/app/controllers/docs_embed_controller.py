from fastapi import APIRouter
from app.services.features import docs_embed_service as service

router = APIRouter(prefix='/docs-embed', tags=['docs-embed'])

router.get('/debug-path')(service.debug_path)
router.get('/by-id/{doc_id}')(service.get_file_meta_by_id)
router.get('/by-object/{object_id}')(service.get_file_meta_by_object_id)
router.get('/by-ai-document/{ai_document_id}')(service.get_file_meta_by_ai_document_id)
router.get('/by-name')(service.get_file_meta_by_name)
router.get('/synap/job')(service.get_synap_job)
router.post('/by-object/{object_id}/renew-url')(service.renew_signed_url_by_object_id)
router.post('/by-id/{doc_id}/renew-url')(service.renew_signed_url_by_id)
router.post('/{stored_filename:path}/renew-url')(service.renew_signed_url)
router.get('/by-object/{object_id}/stream')(service.stream_file_by_object)
router.get('/{stored_filename:path}/stream')(service.stream_file)
router.get('/{stored_filename:path}')(service.get_file_meta)

__all__ = ['router']
