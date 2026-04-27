from fastapi import APIRouter, Query

from app.services import uploads_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/lookup")
def lookup_upload(title: str = Query(..., min_length=1)):
    return uploads_service.lookup_upload(title=title)

