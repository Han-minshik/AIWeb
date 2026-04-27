# React/Vite 개발 서버(3000) 전용 Dockerfile

FROM node:20
WORKDIR /app

# 의존성 설치 (lock 일치 시 npm ci, 불일치 시 npm install로 폴백)
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm sh -c '\
  if [ -f package-lock.json ]; then \
    npm ci || (echo "Lock out of sync → falling back to npm install" && rm -f package-lock.json && npm install); \
  else \
    npm install; \
  fi'

# 소스 복사
COPY . .

# Windows/도커 파일 변경 감지 안정화
ENV CHOKIDAR_USEPOLLING=true
ENV WATCHPACK_POLLING=true

# Vite를 컨테이너 밖에서 접근 가능하게 + 포트 3000으로 고정
ENV HOST=0.0.0.0
ENV PORT=3000
  
# 개발 서버 포트
EXPOSE 3000

# dev 서버 실행 (Vite: host/port 지정)
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
