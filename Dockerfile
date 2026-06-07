FROM python:3.11-slim

# 로그 즉시 출력, .pyc 생성 안 함
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드
COPY app/ ./app/

# 비루트 유저로 실행
RUN useradd -m -u 1000 appuser
USER appuser

CMD ["python", "-m", "app.main"]
