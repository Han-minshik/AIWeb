from fastapi import APIRouter
from app.services.features import auth_service as service

router = APIRouter(prefix='/auth', tags=['auth'])

router.post('/login')(service.login)
router.post('/logout')(service.logout)
router.get('/me')(service.me)
router.get('/admin/check')(service.admin_check)
router.get('/users/me')(service.users_me_alias)

__all__ = ['router']
