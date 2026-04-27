from fastapi import APIRouter
from app.services.features import docs_service as service

router = APIRouter(prefix='/docs', tags=['docs'])

router.get('')(service.list_docs)
router.get('/{doc_id}')(service.get_doc)
router.get('/{doc_id}/revisions')(service.list_doc_revisions)
router.post('/{doc_id}/revisions/reorder')(service.reorder_doc_revisions)
router.post('/upload')(service.upload_doc)
router.post('/{doc_id}/upload-version')(service.upload_doc_version)
router.post('/{doc_id}/delete')(service.delete_doc)
router.patch('/{doc_id}')(service.update_doc)
router.get('/{doc_id}/download')(service.download_doc)
router.post('/{doc_id}/copy')(service.copy_doc)
router.post('/{doc_id}/move')(service.move_doc)
router.post('/create-note')(service.create_note)
router.post('/reorder')(service.reorder_tree_nodes)
router.get('/bookmarks/search')(service.search_bookmarked_docs)

__all__ = ['router']
