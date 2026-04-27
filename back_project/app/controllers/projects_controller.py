from fastapi import APIRouter
from app.services.features import projects_service as service

router = APIRouter(prefix='/projects', tags=['projects'])

router.get('')(service.list_projects)
router.post('')(service.create_project)
router.get('/{project_id}/tree')(service.get_tree)
router.get('/{project_id}/files')(service.list_project_files)
router.post('/{project_id}/nodes')(service.create_node)
router.patch('/nodes/{node_id}')(service.patch_node)
router.delete('/{project_id}/nodes:bulk')(service.bulk_delete)
router.post('/{project_id}/nodes:move')(service.bulk_move)
router.post('/{project_id}/nodes:reorder')(service.reorder_nodes)

__all__ = ['router']
