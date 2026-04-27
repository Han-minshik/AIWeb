from fastapi import APIRouter
from app.services.features import kb_service as service

router = APIRouter(prefix='/kb', tags=['kb'])

router.get('/trees')(service.list_kb_trees)
router.get('/trees/{kb_id}/tree')(service.get_kb_tree)
router.post('/trees/{kb_id}/nodes', status_code=201)(service.create_kb_node)
router.patch('/trees/nodes/{node_id}')(service.patch_kb_node)
router.delete('/trees/{kb_id}/nodes:bulk')(service.delete_kb_nodes_bulk)
router.delete('/trees/{kb_id}/nodes/bulk')(service.delete_kb_nodes_bulk_safe)
router.post('/trees/{kb_id}/nodes/bulk-delete')(service.delete_kb_nodes_bulk_post)
router.post('/trees/{kb_id}/nodes:reorder')(service.reorder_kb_nodes)
router.post('/trees')(service.create_kb_tree)
router.delete('/trees/{kb_id}')(service.delete_kb_tree)

__all__ = ['router']
