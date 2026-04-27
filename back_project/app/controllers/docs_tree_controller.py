from fastapi import APIRouter
from app.services.features import docs_tree_service as service

router = APIRouter(prefix='/docs-tree', tags=['docs-tree'])

router.get('/tree-data')(service.get_tree)
router.post('/batch-rename')(service.batch_rename)
router.post('/batch-delete')(service.batch_delete)
router.post('/reorder')(service.reorder_nodes)
router.get('/files')(service.list_file_nodes)

__all__ = ['router']
