from fastapi import APIRouter

from app.services import list_service

router = APIRouter(prefix="/list", tags=["list"])


@router.get("/storage")
def list_storage():
    return list_service.list_storage()


@router.get("/storage.csv")
def list_storage_csv():
    return list_service.list_storage_csv()


@router.get("/table/{table_name}")
def list_table(table_name: str):
    return list_service.list_table(table_name=table_name)

