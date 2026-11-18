# 문서 schema / stage 규칙

## GENERAL 문서

| 위치 | document_schema | document_stage |
|---|---|---|
| SE KB 루트 바로 아래 | general | NULL |
| 개인 KB 루트 바로 아래 | personal | NULL |
| SE KB 또는 개인 KB의 하위 폴더 | KB 루트 바로 아래 최상위 폴더 표시명 정규화 | NULL |

루트 기본값은 폴더 표시명이나 사용자 이름이 아닌 tree_nodes.kb_type으로 결정합니다.
personal/person이면 personal, 그 밖의 GENERAL KB 루트는 general입니다.
하위 폴더는 alias가 있으면 alias, 없으면 name을 사용합니다.
신규 업로드, 문서 이동·분류명 변경 후 갱신, 일회성 마이그레이션은 동일한 resolver를 사용합니다.
루트의 직속 문서도 분류 폴더로 옮기지 않고 처리합니다.
트리 누락·순환·다른 KB로 연결된 계층·모호한 문서 배치는 오류로 보고합니다.

정규화: 앞뒤 공백 제거, 선행 분류 번호(예: 1.) 제거, casefold,
공백·특수문자를 밑줄로 치환, 연속/앞뒤 밑줄 제거.
한글·숫자는 유지합니다. 결과가 비거나 100자를 초과하면 오류입니다.

## KIWIX 문서

schema/stage 문자열은 그대로 보존합니다. 누락/JSON null만 DB NULL입니다.
빈 문자열과 공백 문자열을 보존하며 숫자·배열·객체·불리언은 허용하지 않습니다.
필수값은 folderName, fileName, fileCreate입니다.
schema, stage, docCode, fileExt는 선택이며 기본 확장자는 md입니다.
원본 JSON 전체를 저장하지 않고 웹 서버 생성 manifest의 source_metadata는 빈 객체입니다.
동일 파일명 매칭·부모 폴더 기반 저장명 정책은 유지합니다.

## DB와 외부 계약

ai_document_info.document_schema / document_stage는 TEXT NULL입니다.
manifest JSON 최상위 document_metadata.schema / stage에 같은 값을 저장합니다.
parse/index 요청은 document.branch_type 및 document.metadata.schema / stage를 포함합니다.
외부 GENERAL 파서는 요청 값을 manifest.document_metadata에 그대로 기록해야 합니다.
인덱서는 값을 재정규화하거나 절삭하지 않고 반영합니다.
검색 인덱스의 기존 데이터 보정은 별도 작업입니다.

## 오프라인 마이그레이션

백업 및 유지보수 종료 후 PostgreSQL과 MinIO가 접근 가능한 상태에서 실행합니다.
활성 KIWIX 문서는 0건, pending/running/sent 파이프라인 작업은 없어야 합니다.
이번 루트 정책은 프로그램이 가져오는 document_metadata_service.py에 포함되므로 이 파일도 함께 배포해야 합니다.

back_project 디렉터리에서 Windows:
```powershell
.\venv\Scripts\python.exe .\tools\document_metadata_migration.py plan
.\venv\Scripts\python.exe .\tools\document_metadata_migration.py apply
.\venv\Scripts\python.exe .\tools\document_metadata_migration.py verify
```

Linux 배포 환경에서는 프로젝트의 Python 실행 환경을 사용합니다:
```sh
python tools/document_metadata_migration.py plan
python tools/document_metadata_migration.py apply
python tools/document_metadata_migration.py verify
```

plan에서 GENERAL 루트 직속 문서가 더 이상 분류 오류를 일으키지 않는지 확인합니다.
이미 적용된 문서는 상태가 일치하면 SKIPPED됩니다.
일회성 도구는 파싱/재인덱싱 요청을 생성하지 않습니다.
DB_UPDATE_PENDING이 있으면 해당 보고서를 지정한 apply --resume 복구 후 verify합니다.
DB 컬럼이 없는 환경에서는 새 백엔드 실행 전에 마이그레이션이 필요합니다.
