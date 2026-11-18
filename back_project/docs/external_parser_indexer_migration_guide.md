# 파서·인덱서 외부 연동 변경 및 이관 가이드

이 문서는 기존 로컬 파일 기반 연동을 MinIO 기반 비동기 파이프라인으로 변경하기 위한 외부 담당자 전달 문서다. 여기에 적힌 규격은 현재 백엔드 구현을 기준으로 한다.

## 1. 변경 전 → 변경 후

| 구분 | 기존 | 변경 후 |
|---|---|---|
| 원본 전달 | `D:\storage\...` 로컬 경로 | MinIO `bucket_name`, `object_key`, `version_id` |
| 파싱 결과 | 서버 로컬 폴더 | Artifact 전용 MinIO 버킷 |
| 처리 방식 | 요청 연결에서 결과를 기다리거나 수동 처리 | 작업 접수 후 비동기 처리, 완료·실패 Callback |
| 접수 의미 | 응답과 완료 상태가 혼재 | 최초 `2xx`는 접수만 의미하고 DB는 `sent` 처리 |
| 완료 의미 | 요청 응답으로 판단 | Callback 성공을 받은 뒤 `completed` 처리 |
| 요청 인증 | 기존 인증 방식 | 백엔드 RS256 JWT 검증 |
| Callback 인증 | 기존 공통 방식 또는 미적용 | 파서·인덱서별 RS256 JWT 서명 |
| 중복 방지 | 명확한 공통 키 없음 | `Idempotency-Key` 기준 중복 실행 방지 |
| 문서 경로 | 실제 파일시스템 폴더 | DB의 웹 논리 경로를 메타데이터로 전달 |
| 버전 선택 | 로컬 파일 또는 최신 파일 | 요청에 지정된 정확한 MinIO `version_id` |
| 인덱싱 메타데이터 | 문서 식별 정보 중심 | 이름·논리 경로·소유자·그룹·ACL·요청자 권한 포함 |
| 이름·경로·ACL 변경 | 별도 처리 | 동일 문서 재인덱싱 요청 |
| 삭제 | 기존 삭제 규격 | `stage=delete` 작업 접수 후 완료 Callback |
| DB 수정 | 외부 처리와 혼재 가능 | 외부 서비스는 DB를 수정하지 않고 백엔드만 수정 |

기존 `/ai-index/callback`은 더 이상 사용하지 않는다. 현재 백엔드는 이 경로에 `410 Gone`을 반환하며 `/pipeline/indexer/callback` 사용을 요구한다.

### KIWIX 원본 처리 원칙

KIWIX도 파일 단위 MinIO 파이프라인을 사용한다. 단, `branch_type=kiwix`인 경로의 Markdown은 이미 파싱된 콘텐츠이므로 외부 파서에 전달하지 않는다.

- 신규 개별 업로드에는 서버 로컬 `documents.json`을 사용하지 않는다.
- 기존 KIWIX 대량 이관에서는 관리자 화면의 **Import KIWIX folder**로 `documents.json`과 모든 `prjCode` 폴더를 함께 선택한다.
- 백엔드 서버가 고정 로컬 경로를 읽는 방식이 아니라, 브라우저가 선택한 `documents.json`을 기준으로 파일을 매칭한다.
- 트리는 `0. KIWIX/{prjCode}/문서` 형태로 생성하며 같은 `prjCode`의 문서는 같은 폴더에 저장한다.
- `docCode`, `section`, `prjName`, `itemtypeName`, `fileCreate` 등 원본 메타데이터는 업로드 요청과 Artifact manifest에 보존한다.

- KIWIX 여부는 DB의 `branch_type=kiwix` 메타데이터로 구분한다.
- KIWIX 경로의 `.md`는 PDF 변환이 성공한 경우에만 등록한다.
- 변환된 PDF는 원본 버킷에 저장하고 기존 공통 뷰어로 열람한다.
- 원본 Markdown, 참조 자산과 대표 `manifest.json`은 리비전별 Artifact Prefix에 저장한다.
- Markdown의 파싱 작업은 백엔드가 `completed`로 기록하고 완료 출처를 `preparsed_markdown`으로 남긴다.
- 대표 Manifest를 사용하는 인덱싱 작업은 `pending`으로 생성한다.
- 변환 또는 저장에 실패하면 해당 문서의 DB 행과 MinIO 객체를 남기지 않고 문서명과 간단한 실패 사유를 반환한다.
- KIWIX 경로의 `.md` 이외 파일은 원본 버킷에 저장하고 GENERAL 문서와 동일하게 외부 파서와 인덱서를 순서대로 거친다.
- DB 문서명과 논리 경로는 기존 KIWIX 저장 규칙을 사용하며 Markdown 문서의 표시 파일명과 MIME은 실제 PDF에 맞춘다.
- 최초 이관에서는 기존 상대 이미지 경로와 폴더 구조를 유지하고 시험에서 확인된 오류에 따라 경로 치환 규칙을 보완한다.
- 이름, 논리 경로, ACL, 삭제 및 재인덱싱 생명주기는 다른 SE KB 문서와 동일하다.
- 기존 로컬 KIWIX 문서는 이관이 끝날 때까지만 읽기 호환 대상으로 유지한다.

## 1-1. 현재 MinIO 저장 구조

웹 백엔드는 서버의 로컬 파일 경로를 파서·인덱서에 전달하지 않는다. 모든 서비스는 아래 MinIO 좌표로 동일한 객체를 식별한다.

| 구분 | 원본 버킷/객체 키 | Artifact 버킷/Prefix |
|---|---|---|
| SE KB·관리자 KB | `MINIO_SE_KB_BUCKET/documents/{object_id}` | `MINIO_SE_KB_ARTIFACT_BUCKET/documents/{object_id}/revisions/{revision_id}/` |
| 개인 KB | `MINIO_PERSONAL_BUCKET/users/{user_id}/documents/{object_id}` | `MINIO_PERSONAL_ARTIFACT_BUCKET/users/{user_id}/documents/{object_id}/revisions/{revision_id}/` |

- 원본 객체는 문서 ID를 안정적인 key로 사용하고, 같은 문서의 새 버전은 같은 key의 새 MinIO `version_id`로 저장한다.
- DB의 각 revision은 정확한 `bucket_name`, `object_key`, `version_id`, `size_bytes`, `checksum`을 가진다.
- 다운로드·뷰어·파서 요청은 항상 DB에 기록된 해당 revision의 MinIO 버전을 사용한다.
- Artifact는 revision별 Prefix를 사용하므로 서로 다른 문서 버전의 결과가 섞이지 않는다.
- 문서의 논리 경로와 표시 이름은 DB 메타데이터로 관리하며 MinIO key를 폴더명으로 사용하지 않는다.

### GENERAL 및 일반 파일

1. 웹이 원본 파일을 원본 버킷에 저장한다.
2. DB에 문서와 revision을 기록하고 `parse` 작업을 `pending`으로 생성한다.
3. 파서에는 원본의 정확한 MinIO 버전과 파서가 결과를 써야 할 revision Artifact Prefix를 전달한다.
4. 파서는 Prefix 아래에 파싱 결과와 대표 `manifest.json`을 저장하고 Callback을 보낸다.
5. 웹은 Callback의 bucket/key/version/size/checksum을 검증한 뒤 대표 manifest를 가리키는 `index` 작업을 생성한다.
6. 인덱서는 manifest를 읽어 OpenSearch에 색인하고 완료 Callback을 보낸다.

### KIWIX Markdown

KIWIX Markdown은 이미 추출된 텍스트이므로 외부 파서를 다시 호출하지 않는다.

1. 관리자 화면에서 `documents.json`과 하위 프로젝트 폴더 전체를 함께 선택한다.
2. 브라우저가 `documents.json`의 `prjCode`, `docCode`, `fileName`을 기준으로 파일과 프로젝트를 매칭한다.
3. `0. KIWIX/{prjCode}` 폴더를 만들고 같은 `prjCode` 문서를 같은 폴더에 배치한다.
4. Markdown을 HTML로 렌더링하고, 상대 이미지 경로(예: `./images/a.jpg`)를 선택된 업로드 파일과 매칭한다.
5. HTML에는 이미지를 data URI로 포함하여 단독으로 열어도 표시되는 self-contained HTML을 만든다.
6. 렌더링된 HTML을 PDF로 변환하고, 이 PDF를 일반 뷰어가 여는 원본 객체로 저장한다.
7. revision Artifact Prefix에는 아래 항목을 저장한다.

```text
documents/{object_id}/revisions/{revision_id}/
├─ content.md                  # 업로드한 원본 Markdown
├─ content.html                # Markdown을 렌더링한 self-contained HTML
├─ assets/{상대 경로}          # Markdown에서 참조한 이미지·자산
└─ manifest.json               # 위 객체와 documents.json 메타데이터의 위치·버전
```

개인 KB용 KIWIX 처리가 추가되는 경우 Prefix 앞에 `users/{user_id}/`가 붙는다. 현재 관리자 KIWIX 적재는 SE KB Artifact 버킷을 사용한다.

현재 KIWIX `manifest.json`의 핵심 구조는 다음과 같다.

```json
{
  "schema_version": "1",
  "ingestion_mode": "preparsed",
  "source_format": "markdown",
  "source_metadata": {
    "prjCode": "documents.json 값",
    "docCode": "documents.json 값",
    "fileName": "documents.json 값"
  },
  "content": {"object_key": ".../content.md", "version_id": "..."},
  "rendered_html": {"object_key": ".../content.html", "version_id": "..."},
  "viewer": {"bucket_name": "원본 버킷", "object_key": "documents/{object_id}", "version_id": "PDF 버전"},
  "assets": [
    {"source_path": "images/a.jpg", "object_key": ".../assets/images/a.jpg", "version_id": "..."}
  ]
}
```

- 실제 `source_metadata`에는 업로드 시 전달된 `documents.json` 메타데이터를 보존한다.
- Markdown 내부에서 참조한 이미지가 선택 파일에 없으면 변환을 실패 처리한다.
- 변환 또는 저장 실패 시 생성한 원본, Artifact, 작업 및 DB 문서 데이터를 정리한다.
- 내부 파싱 작업은 `completed`, parser 표시는 `preparsed_markdown`으로 기록하고 곧바로 외부 인덱싱 작업을 생성한다.
- KIWIX 경로의 Markdown 외 파일(PDF, 이미지, 문서 파일 등)은 GENERAL과 같은 외부 파서 → 인덱서 흐름을 사용한다.

## 1-2. Parser·Indexer·RAG·OpenSearch 역할과 호출 방향

```text
브라우저
  ├─ 문서 업로드/관리 ─> Nginx ─> 웹 백엔드 ─> MinIO + MariaDB
  └─ QA SSE ──────────> Nginx ─> 웹 백엔드 ─> RAG/Search 서비스 ─> OpenSearch

웹 백엔드 ── parse 작업 + MinIO 좌표 ──> Parser
Parser ── Artifact 저장 ──> MinIO Artifact 버킷
Parser ── 완료/실패 Callback ──> Nginx ─> 웹 백엔드

웹 백엔드 ── index/delete 작업 + manifest 좌표 ──> Indexer
Indexer ── 색인 생성/삭제 ──> OpenSearch
Indexer ── 완료/실패 Callback ──> Nginx ─> 웹 백엔드
```

### Parser

- GENERAL 파일의 원본을 정확한 MinIO `version_id`로 읽는다.
- 지정된 Artifact Prefix에 파싱 결과와 대표 manifest를 저장한다.
- 저장한 Artifact의 정확한 MinIO 좌표와 체크섬을 Callback으로 반환한다.
- KIWIX Markdown은 웹에서 이미 Markdown/HTML/PDF/asset/manifest를 만들기 때문에 파서 요청 대상이 아니다.

### Indexer

- 웹이 전달한 대표 manifest를 기준으로 파싱 결과를 읽고 OpenSearch에 색인한다.
- `stage=index`는 색인 생성·갱신, `stage=delete`는 해당 문서/버전의 검색 데이터 삭제에 사용한다.
- 삭제 URL을 공통 `/pipeline/jobs`로 할지 별도 endpoint로 할지는 담당자와 최종 합의가 필요하다.
- 웹은 OpenSearch에 직접 색인하거나 삭제하지 않으며, 인덱서 Callback을 받아 DB 작업 상태를 확정한다.

### RAG

- 브라우저의 `/api/qa/stream` 요청은 Nginx를 거쳐 웹 백엔드로 들어온다.
- 웹 백엔드는 요청 사용자가 선택한 문서를 실제로 사용할 수 있는지 DB 권한을 먼저 검사한다.
- 허용된 `object_id`를 `ai_document_id`로 매핑한 뒤 RAG 서비스의 `/rag/agentic`으로 POST하고 SSE 응답을 다시 브라우저에 중계한다.
- 일반 대화 모드는 같은 RAG 호스트의 `/chat/general`을 사용한다.
- 서비스 인증은 `Authorization: Bearer {RAG_SSE_TOKEN}`, 사용자·ACL 전달은 `X-SpaceAI-User-Token: Bearer {RS256 JWT}`를 사용한다.
- 사용자 JWT에는 `sub`, `group_id`, `position_id`, `is_admin`, `scope=["rag:query"]`, 짧은 만료시간이 포함된다.

### OpenSearch

- 문서 색인과 삭제는 인덱서가 수행하고, 검색 질의는 RAG/Search 서비스가 수행한다.
- 웹 백엔드는 OpenSearch에 직접 연결하지 않는다.
- 대시보드의 OpenSearch/활동/최근 통계는 RAG URL과 같은 호스트의 `/stats/opensearch`, `/stats/activity`, `/stats/recent`를 웹 백엔드가 인증 헤더와 함께 호출하여 프록시한다.
- 따라서 화면의 “통계 서버에 연결할 수 없습니다” 오류는 웹 로그인 문제가 아니라 RAG/Search 통계 API가 미서빙·오접속·인증 실패한 경우에도 발생할 수 있다.

## 2. 비동기 처리 규칙

1. 백엔드가 파서 또는 인덱서 작업 접수 API를 호출한다.
2. 외부 서버는 요청을 검증하고 자체 작업 큐에 저장한다.
3. 정상 접수되면 가능한 한 빠르게 `202 Accepted`를 반환한다.
4. 백엔드는 모든 `2xx`를 정상 접수로 판단하고 작업 상태를 `sent`로 변경한다.
5. 외부 서버는 HTTP 연결과 무관하게 실제 작업을 계속 수행한다.
6. 완료 또는 실패 시 외부 서버가 백엔드 Callback을 호출한다.
7. 백엔드는 Callback을 검증한 뒤에만 `completed` 또는 `failed`로 변경한다.

권장 접수 응답:

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "accepted": true,
  "task_id": "external-task-id"
}
```

응답의 `task_id`, `external_task_id`, `job_id` 중 하나가 있으면 백엔드가 외부 작업 ID로 저장한다. 응답 본문은 선택 사항이지만 `task_id` 반환을 권장한다.

파싱·인덱싱이 수 시간 걸리더라도 접수 HTTP 연결은 유지하지 않는다. 현재 백엔드의 접수 요청 제한은 연결 5초, 접수 응답 30초이며 실제 작업 시간 제한이 아니다.

## 3. 백엔드 → 외부 서버 JWT

백엔드는 `backend-service/private.pem`으로 요청 JWT를 서명한다. 파서와 인덱서는 전달받은 `backend-service/public.pem`으로 검증한다.

JWT Header:

```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "spaceai-backend-01"
}
```

공통 Claim:

```json
{
  "iss": "spaceai-backend",
  "aud": "spaceai-pipeline",
  "sub": "spaceai-backend",
  "target_service": "parser 또는 indexer",
  "scope": ["parser:jobs 또는 indexer:jobs"],
  "iat": 0,
  "nbf": 0,
  "exp": 0,
  "jti": "고유 UUID",
  "job_id": 900,
  "stage": "parse 또는 index 또는 delete",
  "object_id": 348,
  "revision_id": 101
}
```

- 기본 TTL: 300초
- 구현상 허용 설정 범위: 최소 30초, 최대 900초
- 파서 필수 Scope: `parser:jobs`
- 인덱서 필수 Scope: `indexer:jobs`
- `iss`, `aud`, `sub`, `target_service`, `scope`, `iat`, `nbf`, `exp`, `jti`를 검증한다.
- 서버 간 시각을 동기화한다.

요청 Header:

```http
Authorization: Bearer <BACKEND_SERVICE_JWT>
Idempotency-Key: pipeline:<job_id>:<stage>:<revision_id 또는 0>
Content-Type: application/json
```

예시:

```text
pipeline:900:parse:101
pipeline:901:index:101
pipeline:902:delete:0
```

동일한 `Idempotency-Key`가 다시 들어오면 새로운 작업을 생성하지 말고 기존 접수 결과를 반환한다.

## 4. 공통 작업 요청 필드

```json
{
  "job_id": 900,
  "idempotency_key": "pipeline:900:parse:101",
  "stage": "parse",
  "action": "add",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "artifact_id": null,
  "document": {
    "name": "문서.pdf",
    "logical_path": "개인 KB/업무/문서.pdf",
    "owner_user_id": 15,
    "kb_type": "personal",
    "mime_type": "application/pdf",
    "size_bytes": 123456,
    "checksum_sha256": "64자리 SHA-256",
    "acl": {
      "entries": [],
      "requester_permissions": ["r", "w"]
    }
  },
  "callback": {
    "url": "<외부 서버에서 접근 가능한 Callback URL>",
    "method": "POST",
    "authentication": "Bearer JWT"
  }
}
```

`action`은 파이프라인 단계가 아니다. 단계는 `stage`로 판단한다. 실제 `action`에는 작업 생성 원인에 따라 `add`, `update`, `reindex`, `delete` 등이 전달될 수 있다.

ACL Entry 구조:

```json
{
  "acl_id": 1,
  "policy_code": "정책 코드",
  "owner_user_id": 15,
  "target_group_id": 3,
  "target_group_name": "그룹명",
  "owner_r": 1,
  "owner_w": 1,
  "owner_x": 1,
  "group_r": 1,
  "group_w": 0,
  "group_x": 0,
  "other_r": 0,
  "other_w": 0,
  "other_x": 0
}
```

## 5. 파서 요청

파서 요청에는 공통 필드와 `source`, `artifact_target`이 추가된다.

```json
{
  "job_id": 900,
  "idempotency_key": "pipeline:900:parse:101",
  "stage": "parse",
  "action": "add",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "artifact_id": null,
  "source": {
    "bucket_name": "spaceai-personal",
    "object_key": "users/15/documents/348",
    "version_id": "exact-source-version-id"
  },
  "artifact_target": {
    "bucket_name": "spaceai-personal-artifacts",
    "prefix": "users/15/documents/348/revisions/101/"
  },
  "document": {
    "name": "문서.pdf",
    "logical_path": "개인 KB/업무/문서.pdf",
    "owner_user_id": 15,
    "kb_type": "personal",
    "mime_type": "application/pdf",
    "size_bytes": 123456,
    "checksum_sha256": "64자리 SHA-256",
    "acl": {
      "entries": [],
      "requester_permissions": ["r", "w"]
    }
  },
  "callback": {
    "url": "<BACKEND_REACHABLE_BASE_URL>/api/pipeline/parser/callback",
    "method": "POST",
    "authentication": "Bearer JWT"
  }
}
```

파서는 반드시 `source.version_id`의 정확한 원본을 읽는다. 최신 버전을 임의 선택하지 않는다.

Artifact는 반드시 할당받은 버킷과 Prefix 아래에 생성한다.

```text
SE KB:     documents/{object_id}/revisions/{revision_id}/
개인 KB:   users/{user_id}/documents/{object_id}/revisions/{revision_id}/
```

웹의 논리 경로를 Artifact 객체 키로 사용하지 않는다. 새 리비전은 기존 Prefix를 덮어쓰지 않고 새 리비전 Prefix에 생성한다.

## 6. 파서 성공 Callback

Callback URL:

```text
<BACKEND_REACHABLE_BASE_URL>/api/pipeline/parser/callback
```

성공 요청:

```json
{
  "event_id": "parser-900-completed-1",
  "job_id": 900,
  "status": "completed",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "external_task_id": "parser-task-123",
  "parser_version": "parser-version",
  "artifacts": [
    {
      "artifact_type": "manifest",
      "primary": true,
      "bucket_name": "spaceai-personal-artifacts",
      "object_key": "users/15/documents/348/revisions/101/manifest.json",
      "minio_version_id": "artifact-version-id",
      "size_bytes": 12345,
      "checksum_sha256": "64자리 SHA-256"
    }
  ]
}
```

중요:

- 대표 여부 필드명은 `primary`다. `is_primary`가 아니다.
- 버전 필드는 `minio_version_id` 사용을 권장한다. 백엔드는 호환 목적으로 `version_id`도 받는다.
- 성공 Callback에는 Artifact가 1개 이상 필요하다.
- Artifact는 최대 100개다.
- 정확히 하나만 `primary=true`여야 한다.
- Artifact가 하나뿐이고 `primary`가 생략되면 백엔드가 대표 Artifact로 처리한다.
- 대표 Artifact의 `artifact_type`은 반드시 `manifest`다.
- `artifact_type`은 1~32자다.
- `checksum_sha256`은 64자리 16진수다.
- `size_bytes`는 0 이상의 정수다.
- 객체는 할당된 Artifact 버킷과 리비전 Prefix 안에 있어야 한다.
- 백엔드는 Callback을 적용하기 전에 MinIO 객체, 정확한 버전, 크기와 체크섬 메타데이터를 검증한다.

성공 상태로 인식하는 값:

```text
success, succeeded, completed, done, ok
```

## 7. 파서 실패 Callback

```json
{
  "event_id": "parser-900-failed-1",
  "job_id": 900,
  "status": "failed",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "external_task_id": "parser-task-123",
  "error_code": "PARSER_ERROR",
  "error_message": "실패 원인"
}
```

실패 상태로 인식하는 값:

```text
failed, fail, error
```

## 8. 인덱서 요청

인덱싱 요청에는 검증 완료된 대표 Artifact가 전달된다.

```json
{
  "job_id": 901,
  "idempotency_key": "pipeline:901:index:101",
  "stage": "index",
  "action": "add",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "item_type_code": 10,
  "revision_id": 101,
  "artifact_id": 501,
  "artifact_source": {
    "bucket_name": "spaceai-personal-artifacts",
    "object_key": "users/15/documents/348/revisions/101/manifest.json",
    "version_id": "exact-artifact-version-id",
    "artifact_type": "manifest"
  },
  "document": {
    "name": "문서.pdf",
    "logical_path": "개인 KB/업무/문서.pdf",
    "owner_user_id": 15,
    "kb_type": "personal",
    "mime_type": "application/pdf",
    "size_bytes": 123456,
    "checksum_sha256": "64자리 SHA-256",
    "acl": {
      "entries": [],
      "requester_permissions": ["r", "w"]
    }
  },
  "callback": {
    "url": "<BACKEND_REACHABLE_BASE_URL>/api/pipeline/indexer/callback",
    "method": "POST",
    "authentication": "Bearer JWT"
  }
}
```

인덱서는 `artifact_source.version_id`의 정확한 Artifact를 읽는다. 검색 인덱스에는 `object_id`, `ai_document_id`, `revision_id`, `artifact_id`, 이름, 논리 경로, 소유자, 그룹, ACL과 필요한 권한 메타데이터를 반영한다.

문서 이름·논리 경로·ACL이 변경되면 백엔드가 `update` 또는 `reindex` 작업을 생성할 수 있다.

## 9. 인덱스 삭제 요청

삭제 작업은 해당 문서의 인덱싱 요청이 외부로 전달됐거나 성공한 이력이 있을 때만 인덱서 작업 접수 URL로 전달된다.

- 파싱·인덱싱 작업이 아직 `pending`이고 외부로 전달되지 않았다면 해당 작업만 `cancelled` 처리한다.
- 파싱 결과 Artifact는 백엔드가 MinIO에서 정리하므로, 파서에 전달되지 않은 작업에 별도 삭제 요청을 보내지 않는다.
- 인덱싱 요청을 한 번도 외부에 전달하지 않았고 `indexed_revision_id`도 없다면 인덱서 삭제 요청 없이 백엔드가 MinIO와 DB 영구 삭제를 진행한다.
- 인덱싱 작업이 실제로 시작·전송·완료됐거나 외부 작업 ID 또는 성공한 인덱싱 리비전이 있으면 인덱서 삭제 성공 Callback 후 영구 삭제한다.

```json
{
  "job_id": 902,
  "idempotency_key": "pipeline:902:delete:0",
  "stage": "delete",
  "action": "delete",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": null,
  "artifact_id": null,
  "document": {
    "name": "문서.pdf",
    "logical_path": "개인 KB/업무/문서.pdf",
    "owner_user_id": 15,
    "kb_type": "personal",
    "mime_type": "application/pdf",
    "size_bytes": 123456,
    "checksum_sha256": "64자리 SHA-256",
    "acl": {
      "entries": [],
      "requester_permissions": []
    }
  },
  "callback": {
    "url": "<BACKEND_REACHABLE_BASE_URL>/api/pipeline/indexer/callback",
    "method": "POST",
    "authentication": "Bearer JWT"
  }
}
```

인덱서가 검색 인덱스 삭제를 완료한 뒤 성공 Callback을 보내면 백엔드가 후속 영구 삭제를 진행한다. 외부 서비스가 백엔드 DB를 직접 삭제하면 안 된다.

## 10. 인덱서 Callback

Callback URL:

```text
<BACKEND_REACHABLE_BASE_URL>/api/pipeline/indexer/callback
```

인덱싱 성공 예시:

```json
{
  "event_id": "indexer-901-completed-1",
  "job_id": 901,
  "status": "completed",
  "action": "add",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "artifact_id": 501,
  "external_task_id": "indexer-task-456"
}
```

삭제 성공 예시:

```json
{
  "event_id": "indexer-902-deleted-1",
  "job_id": 902,
  "status": "completed",
  "action": "delete",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "external_task_id": "indexer-task-457"
}
```

실패 예시:

```json
{
  "event_id": "indexer-901-failed-1",
  "job_id": 901,
  "status": "failed",
  "action": "add",
  "object_id": 348,
  "ai_document_id": "aid_abcd1234",
  "revision_id": 101,
  "artifact_id": 501,
  "external_task_id": "indexer-task-456",
  "error_code": "INDEX_ERROR",
  "error_message": "실패 원인"
}
```

인덱싱 Callback은 `object_id`가 필수다. `stage=index` 작업은 `revision_id`와 `artifact_id`도 필수다. `ai_document_id`와 `action`을 보내면 DB 작업과 반드시 일치해야 하므로 모든 필드를 그대로 반환하는 것을 권장한다.

인덱서 성공 상태로 인식하는 값:

```text
success, succeeded, completed, indexed, deleted, done, ok
```

실패 상태로 인식하는 값:

```text
failed, fail, error
```

## 11. 외부 서버 → 백엔드 Callback JWT

파서:

```text
alg   = RS256
iss   = spaceai-parser
aud   = spaceai-backend
scope = parser:callback
```

인덱서:

```text
alg   = RS256
iss   = spaceai-indexer
aud   = spaceai-backend
scope = indexer:callback
```

Callback JWT 필수 Claim:

```json
{
  "iss": "서비스별 issuer",
  "aud": "spaceai-backend",
  "scope": "서비스별 callback scope",
  "iat": 0,
  "nbf": 0,
  "exp": 0,
  "jti": "고유 UUID"
}
```

`scope`는 문자열 또는 문자열 배열로 보낼 수 있다. Callback마다 새로운 `jti`와 짧은 만료 시간을 사용하며 TTL 300초를 권장한다.

```http
Authorization: Bearer <SERVICE_CALLBACK_JWT>
Content-Type: application/json
```

## 12. Callback 멱등성과 응답

- `event_id`는 Callback 이벤트마다 고유한 ASCII 문자열이어야 한다.
- 최대 길이는 128자다.
- 같은 결과를 재전송할 때는 동일한 `event_id`와 완전히 동일한 JSON을 사용한다.
- 동일한 `event_id`와 동일한 Payload는 중복 처리되지 않고 정상 응답한다.
- 동일한 `event_id`로 다른 Payload를 보내면 `409 Conflict`가 발생한다.
- 취소·대체된 작업이나 복구 이후 늦게 도착한 삭제 결과는 백엔드가 무시할 수 있다.

주요 응답 코드:

| 상태 | 의미 |
|---|---|
| `200` | 적용, 중복 인정 또는 정책상 무시 완료 |
| `401` | Bearer JWT 누락 또는 검증 실패 |
| `404` | 대응하는 작업 없음 |
| `409` | 작업 식별자 불일치, 이벤트 충돌 또는 MinIO 정합성 오류 |
| `422` | 필수 필드 또는 데이터 형식 오류 |
| `503` | 백엔드가 MinIO Artifact를 검증할 수 없음 |

`5xx` 또는 네트워크 오류에서는 동일한 `event_id`와 Payload로 재시도한다. `4xx`는 요청 규격이나 작업 식별자를 수정하지 않고 무한 재시도하면 안 된다.

## 13. MinIO 연결 정보와 권한

같은 워크스테이션의 Docker 컨테이너에서 접근할 때 현재 예정 주소:

```text
http://host.docker.internal:9100
```

다른 PC 또는 서버로 이동하면 `endpoint`만 실제 MinIO 주소로 변경한다. TLS 적용 시 `https` 주소와 `secure=true`를 사용한다.

### 파서

계정:

```text
parser-reader
```

정책:

```text
parser-readonly
parser-artifact-manager-v3
```

권한 목적:

- SE KB·개인 KB 원본의 정확한 객체 버전 읽기
- SE KB·개인 KB Artifact 생성 및 조회
- Multipart Upload
- 백엔드에서 요청한 Artifact 정리

파서는 원본 버킷 객체를 수정하거나 삭제하면 안 된다. Artifact 삭제 권한도 백엔드가 요청한 작업에만 사용한다.

### 인덱서

계정:

```text
indexer-reader
```

정책:

```text
indexer-artifact-readonly
```

인덱서는 Artifact의 정확한 객체 버전만 조회하며 MinIO 객체를 생성·변경·삭제하지 않는다.

## 14. 담당자별 전달 파일

### 파서 담당자

```text
secret/backend-service/public.pem
secret/callback/parser/private.pem
secret/minio/parser/access_key
secret/minio/parser/secret_key
secret/minio/parser/endpoint
secret/minio/parser/secure
```

### 인덱서 담당자

```text
secret/backend-service/public.pem
secret/callback/indexer/private.pem
secret/minio/indexer/access_key
secret/minio/indexer/secret_key
secret/minio/indexer/endpoint
secret/minio/indexer/secure
```

현재 `secret/minio/parser/secret_key`와 `secret/minio/indexer/secret_key`는 비어 있으므로 전달 전에 실제 값을 입력하거나 계정 Secret Key를 재설정해야 한다.

### 전달 금지

```text
secret/backend-service/private.pem
secret/callback/parser/public.pem
secret/callback/indexer/public.pem
secret/minio/admin/*
secret/minio/backend/*
secret/rag/*
.env 전체
MariaDB 자격증명
```

Callback 개인키와 MinIO Secret Key는 Git, 일반 메일, 메신저 본문 또는 업무 문서에 붙이지 않고 승인된 보안 채널로 전달한다.

외부 서버 적용과 Callback 테스트가 완료된 뒤 백엔드 PC에 남은 다음 파일을 삭제한다.

```text
secret/callback/parser/private.pem
secret/callback/indexer/private.pem
secret/minio/parser/secret_key
secret/minio/indexer/secret_key
```

백엔드는 다음 파일을 계속 보관한다.

```text
secret/backend-service/private.pem
secret/backend-service/public.pem
secret/callback/parser/public.pem
secret/callback/indexer/public.pem
secret/minio/admin/*
secret/minio/backend/*
```

## 14-1. 현재 검증 상태와 배포 단계

2026-09-10 현재 웹 프로젝트 자체 검증 결과는 다음과 같다.

| 검증 항목 | 결과 | 범위 |
|---|---|---|
| 백엔드 단위·Mock 테스트 | 성공 | 105개 테스트 통과 |
| 프런트엔드 운영 빌드 | 성공 | Vite build 완료 |
| MinIO health | 성공 | 로컬 `9100/minio/health/live` → `200 OK` |
| Parser 연결 | 대기 | `8003` 미서빙 |
| Indexer 연결 | 대기 | `8105` 미서빙 |
| RAG/Search·OpenSearch 통계 연결 | 대기 | `8000` 미서빙 |
| Nginx → 웹 백엔드 runtime smoke test | 대기 | 점검 시 백엔드 upstream 미기동으로 `502` |

통과한 Mock 테스트에는 다음 범위가 포함된다.

- Parser·Indexer 작업 요청 payload와 목적지
- RS256 JWT, Callback JWT, 만료·audience·issuer 검사
- `Idempotency-Key` 중복 방지
- Parser 성공·실패 Callback과 후속 index 작업 생성
- index/delete 작업과 문서·버전 삭제 생명주기
- MinIO 원본·Artifact key와 정확한 version 처리
- KIWIX Markdown → HTML → PDF 변환
- KIWIX 이미지 수집, self-contained HTML, assets 및 manifest 생성
- KIWIX 변환 실패 시 MinIO·DB 정리
- 개인 KB 격리와 문서 권한 검사
- RAG 사용자 JWT 생성

현재 Parser·Indexer·RAG/Search 서비스가 이 웹 백엔드와 같은 서버에 없고 아직 서빙되지 않았으므로, 실제 MinIO 읽기/쓰기부터 Callback, OpenSearch 색인·삭제, RAG 검색까지의 End-to-End 완료로 판단하면 안 된다.

배포 및 검증 순서는 다음과 같이 고정한다.

1. Parser·Indexer·RAG/Search 담당 환경에서 각 서비스를 오프라인 또는 로컬로 먼저 서빙한다.
2. 각 서비스 자체 테스트와 MinIO 연결 테스트를 수행하고 발견된 오류를 수정한다.
3. 웹 백엔드와 같은 테스트망에서 실제 endpoint, JWT, MinIO version, Callback을 연결한다.
4. GENERAL 1건과 KIWIX 1건으로 parse/index/RAG/delete 전 과정을 검증한다.
5. 오프라인 서빙 테스트가 끝난 뒤 Parser·Indexer·RAG/Search 서비스를 4090 서버에 서빙한다.
6. 4090 서버의 실제 주소·포트·방화벽·TLS·Nginx callback 접근 경로를 확정한다.
7. 웹 백엔드 환경 변수의 임시 localhost URL을 4090 서버 URL로 교체한다.
8. 운영과 같은 조건에서 E2E 회귀 테스트 후 전환한다.

주의: 현재 Nginx는 개발 PC의 `127.0.0.1:8180`에 바인딩되어 있다. 4090 서버의 외부 서비스가 Callback을 호출하려면 해당 서버에서 접근 가능한 호스트명 또는 IP, 포트, 방화벽 정책과 가능하면 HTTPS 주소를 별도로 제공해야 한다.

## 15. 아직 확정해야 하는 실제 주소

현재 개발용 포트 설정 상태:

```env
PARSER_DISPATCH_URL=http://localhost:8003/parser/jobs
INDEXER_DISPATCH_URL=http://localhost:8105/indexer/jobs
PARSER_CALLBACK_URL=http://host.docker.internal:8180/api/pipeline/parser/callback
INDEXER_CALLBACK_URL=http://host.docker.internal:8180/api/pipeline/indexer/callback
```

파서 포트는 `8003`, 인덱서 포트는 `8105`를 사용한다. 작업 접수 경로는 파서 `/parser/jobs`, 인덱서 `/indexer/jobs`를 사용한다. 호스트 주소는 배포 환경에 맞게 변경한다.

Callback 주소는 같은 워크스테이션의 Docker 컨테이너에서 `host.docker.internal:8180`을 통해 Nginx에 접근하는 개발 구성이다. Docker 컨테이너에서 `/api/openapi.json` 호출이 `200 OK`인 것을 확인했다. 다른 PC 또는 운영 서버로 분리할 때는 실제 호스트명과 HTTPS 주소로 변경해야 한다.

- `localhost`는 요청을 실행하는 시스템 자신을 가리킨다.
- 파서·인덱서가 다른 컨테이너 또는 PC라면 `localhost` Callback 주소에 접근할 수 없다.
- Nginx 외부 경로는 `/api/pipeline/parser/callback`, `/api/pipeline/indexer/callback`이다.
- 현재 구성은 같은 워크스테이션의 Docker 컨테이너에서만 검증되었다. 다른 PC의 외부 서비스에서는 접근 가능한 주소와 방화벽 정책을 별도로 확정해야 한다.

외부 담당자로부터 다음 값을 회신받은 후 `.env`를 변경한다.

```env
PARSER_DISPATCH_URL=<실제 파서 작업 접수 URL>
INDEXER_DISPATCH_URL=<실제 인덱서 작업 접수 URL>
PARSER_CALLBACK_URL=<파서가 접근 가능한 백엔드 주소>/api/pipeline/parser/callback
INDEXER_CALLBACK_URL=<인덱서가 접근 가능한 백엔드 주소>/api/pipeline/indexer/callback
```

## 16. 외부 담당자 회신 체크리스트

- 실제 파서·인덱서 작업 접수 URL
- `202 Accepted` 응답 구현 여부
- 백엔드 JWT 검증 구현 여부
- 서비스별 Callback JWT 서명 구현 여부
- `Idempotency-Key` 중복 방지 구현 여부
- MinIO 연결 및 정확한 Version ID 조회 여부
- 파서의 리비전별 Artifact 생성 여부
- 대표 `manifest` 형식과 내부 스키마
- 인덱서의 Artifact 지원 형식
- 문서 이름·논리 경로·ACL 재인덱싱 지원 여부
- 검색 인덱스 삭제 작업 지원 여부
- Callback 재시도 정책
- 연동 테스트 가능한 일정

## 17. 적용 순서

1. 로컬 Sync UI/API 및 신규 로컬 파일 쓰기 차단
2. GENERAL·KIWIX·개인 KB의 파일 단위 MinIO 업로드와 공통 뷰어 회귀 시험
3. 문서 검토 및 실제 접수 URL 합의
4. Secret Key 입력 후 키·MinIO 자격증명 보안 전달
5. 파서·인덱서 개발 환경에서 JWT와 MinIO 연결 검증
6. Callback 접근 경로와 Nginx 바인딩 확정
7. 백엔드 `.env`에 실제 URL 반영
8. 가짜 Job을 이용한 요청·Callback 계약 시험
9. 개인 KB와 KIWIX 문서 각 1건으로 업로드 → 파싱 → 인덱싱 시험
10. 실패·재시도·중복 Callback 시험
11. 이름·경로·ACL 변경 재인덱싱 시험
12. 논리 삭제·복구·영구 삭제 시험
13. 기존 로컬 문서의 MinIO 이관 완료 후 읽기 호환 코드 제거
14. 검증 후 외부 전달용 개인키와 Secret Key의 백엔드 보관본 삭제
