from fastapi import APIRouter
from app.services.features import bookmarks_service as service

router = APIRouter(prefix='/bookmarks', tags=['bookmarks'])

router.post('')(service.add_bookmark)
router.delete('')(service.remove_bookmark)
router.get('/tree')(service.get_tree)
router.post('/nodes')(service.create_node)
router.patch('/nodes/{node_id}')(service.patch_node)
router.delete('/nodes:bulk')(service.delete_nodes_bulk)

__all__ = ['router']
