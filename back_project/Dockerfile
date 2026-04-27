FROM python:3.11-slim

# 필수 패키지(컴파일/타임존/런타임 라이브러리 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential tzdata curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 파이썬 런타임 기본 설정
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8001 \
    PYTHONPATH=/app

# 의존성 설치
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 소스 복사 (.dockerignore로 불필요/민감 파일 제외)
COPY . .

# 컨테이너에서 업로드/로그 디렉터리 준비
RUN mkdir -p /app/uploads /app/logs

EXPOSE 8001

# ✅ 엔트리 경로를 실제 파일(main.py)과 맞춤
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
