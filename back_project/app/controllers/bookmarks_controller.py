from fastapi import APIRouter
from app.services.features import bookmarks_service as service

router = APIRouter(prefix='/bookmarks', tags=['bookmarks'])

router.post('')(service.add_bookmark)
router.delete('')(service.remove_bookmark)
router.post('/groups')(service.create_group)
router.patch('/groups/{group_id}')(service.rename_group)
router.delete('/groups/{group_id}')(service.delete_group)
router.get('/groups')(service.list_groups)
router.get('/groups/{group_id}/tree')(service.get_tree)
router.post('/groups/{group_id}/nodes')(service.create_node)
router.patch('/nodes/{node_id}')(service.patch_node)
router.delete('/groups/{group_id}/nodes:bulk')(service.delete_nodes_bulk)

__all__ = ['router']
