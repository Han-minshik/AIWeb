from fastapi import APIRouter
from app.services.features import kb_service as service

router = APIRouter(prefix='/kb', tags=['kb'])

router.get('/personal')(service.get_personal_kb)
router.post('/personal', status_code=201)(service.create_personal_kb)
router.get('/personal/deleted-documents')(service.list_personal_deleted_documents)
router.post('/personal/deleted-documents/actions')(service.apply_personal_deleted_document_actions)
router.post('/personal/deleted-documents/{deletion_id}/restore')(service.restore_personal_deleted_document)
router.post('/personal/deleted-documents/{deletion_id}/purge')(service.purge_personal_deleted_document)
router.get('/personal/ai-index/jobs')(service.list_personal_ai_index_jobs)
router.post('/personal/ai-index/jobs/retry')(service.retry_personal_ai_index_jobs)
router.post('/personal/ai-index/jobs/dispatch')(service.dispatch_personal_ai_index_jobs)
router.get('/perm-policies')(service.list_perm_policies)
router.get('/trees')(service.list_kb_trees)
router.get('/trees/{kb_id}/tree')(service.get_kb_tree)
router.post('/trees/{kb_id}/permissions:bulk')(service.update_kb_permissions_bulk)
router.post('/trees/{kb_id}/nodes', status_code=201)(service.create_kb_node)
router.patch('/trees/nodes/{node_id}')(service.patch_kb_node)
router.delete('/trees/{kb_id}/nodes:bulk')(service.delete_kb_nodes_bulk)
router.delete('/trees/{kb_id}/nodes/bulk')(service.delete_kb_nodes_bulk_safe)
router.post('/trees/{kb_id}/nodes/bulk-delete')(service.delete_kb_nodes_bulk_post)
router.post('/trees/{kb_id}/nodes:reorder')(service.reorder_kb_nodes)
router.post('/trees')(service.create_kb_tree)
router.delete('/trees/{kb_id}')(service.delete_kb_tree)

__all__ = ['router']
