from __future__ import annotations

import uuid

from app.repositories.common import fetch_one
from app.services.features import kb_service


def main() -> None:
    root = fetch_one(
        """
        SELECT node_id,kb_root_id,kb_type,kb_owner_user_id
          FROM tree_nodes
         WHERE parent_id IS NULL AND is_folder=TRUE AND kb_type='personal'
         ORDER BY node_id LIMIT 1
        """
    )
    if not root:
        raise RuntimeError('No personal KB is available for hierarchy integration testing')

    root_id = int(root['node_id'])
    user_id = int(root['kb_owner_user_id'])
    prefix = f'__personal_hierarchy_test_{uuid.uuid4().hex[:10]}'
    created: list[int] = []
    try:
        folder_a = kb_service.create_kb_node(
            root_id, kb_service.KBNodeIn(name=f'{prefix}_a', parent_id=root_id), user_id=user_id,
        )
        created.append(int(folder_a['node_id']))
        folder_b = kb_service.create_kb_node(
            root_id, kb_service.KBNodeIn(name=f'{prefix}_b', parent_id=root_id), user_id=user_id,
        )
        created.append(int(folder_b['node_id']))
        child = kb_service.create_kb_node(
            root_id,
            kb_service.KBNodeIn(name=f'{prefix}_child', parent_id=created[0]),
            user_id=user_id,
        )
        child_id = int(child['node_id'])
        created.append(child_id)

        kb_service.patch_kb_node(
            child_id, kb_service.KBNodePatch(name=f'{prefix}_renamed'), user_id=user_id,
        )
        kb_service.patch_kb_node(
            child_id, kb_service.KBNodePatch(parent_id=created[1]), user_id=user_id,
        )

        row = fetch_one(
            """
            SELECT n.name,n.alias,n.parent_id,ab.policy_code
              FROM tree_nodes n
              LEFT JOIN acl_bindings ab ON ab.node_id=n.node_id
             WHERE n.node_id=%s
            """,
            (child_id,),
        ) or {}
        if row.get('name') != f'{prefix}_renamed' or row.get('alias') != f'{prefix}_renamed':
            raise RuntimeError('Personal folder rename was not persisted')
        if int(row.get('parent_id') or 0) != created[1]:
            raise RuntimeError('Personal folder move was not persisted')
        if str(row.get('policy_code') or '').upper() != 'PRIVATE':
            raise RuntimeError('Personal folder policy is not PRIVATE')

        kb_service.reorder_kb_nodes(
            root_id,
            kb_service.KBNodesReorderIn(parent_id=root_id, orders=[(created[0], 1), (created[1], 0)]),
            user_id=user_id,
        )
        print('Personal KB hierarchy: create/rename/move/reorder/private policy passed')
    finally:
        for node_id in reversed(created):
            if fetch_one('SELECT node_id FROM tree_nodes WHERE node_id=%s', (node_id,)):
                kb_service.delete_kb_nodes_bulk_post(
                    root_id,
                    kb_service.KBNodesBulkDeleteIn(node_ids=[node_id]),
                    user_id=user_id,
                )
        remaining = fetch_one(
            "SELECT COUNT(*) AS n FROM tree_nodes WHERE alias LIKE %s",
            (f'{prefix}%',),
        ) or {}
        if int(remaining.get('n') or 0) != 0:
            raise RuntimeError('Personal hierarchy test records remain after cleanup')
        print('Personal KB hierarchy: cleanup passed')


if __name__ == '__main__':
    main()
