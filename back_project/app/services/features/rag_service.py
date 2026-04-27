import os
import tempfile
from typing import List, Dict, Any
from fastapi import UploadFile, File, Form, Depends, HTTPException
from app.deps.auth_dep import get_current_user
from app.repositories.common import execute
from langchain.vectorstores import FAISS
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from PyPDF2 import PdfReader


def _vector_path(user_id: int) -> str:
    return f"vectors_{user_id}.faiss"

def ingest_docs(files: List[UploadFile] = File(...), user=Depends(get_current_user)):
    embedding = OpenAIEmbeddings()
    db = None
    for file in files:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(file.file.read()); tmp.flush()
            reader = PdfReader(tmp.name)
            texts: List[str] = []
            metas: List[dict] = []
            splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=50)
            for i, page in enumerate(reader.pages, start=1):
                raw = page.extract_text() or ""
                chunks = splitter.split_text(raw)
                for ch in chunks:
                    texts.append(ch)
                    metas.append({"title": file.filename, "page": i})
        part = FAISS.from_texts(texts, embedding, metadatas=metas)
        db = part if db is None else db.merge_from(part)
        os.unlink(tmp.name)
    if not db:
        raise HTTPException(status_code=400, detail="파일 없음")
    db.save_local(_vector_path(user["id"]))
    return {"message": "벡터스토어 저장", "path": _vector_path(user["id"])}

def query_docs(query: str = Form(...), user=Depends(get_current_user)):
    path = _vector_path(user["id"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="벡터스토어 없음")
    embedding = OpenAIEmbeddings()
    db = FAISS.load_local(path, embedding, allow_dangerous_deserialization=True)
    docs = db.similarity_search(query, k=5)

    snippets: List[str] = []
    doc_items: List[Dict[str, Any]] = []
    for d in docs:
        txt = (d.page_content or "").strip()
        meta = d.metadata or {}
        if not txt:
            continue
        snippets.append(txt)
        doc_items.append({
            "title": meta.get("title") or (txt[:60] + "..." if len(txt) > 60 else txt),
            "snippet": txt[:300],
            "page": meta.get("page"),
        })
    answer = "\n\n---\n\n".join(snippets[:3]) if snippets else ""
    return {"answer": answer, "docs": doc_items}
