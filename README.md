# Slack F1 Race Control Notifier

F1 라이브타이밍 SignalR 피드를 실시간으로 구독해서, Race Control Message(옐로 플래그, 세이프티카, 페널티, 조사 등)가 뜨면 Slack 채널로 알림을 보내는 봇.

## 동작 방식

1. F1 공식 라이브타이밍 SignalR Core 엔드포인트(`wss://livetiming.formula1.com/signalrcore`)에 연결
2. `RaceControlMessages` 토픽을 구독
3. 새 메시지가 들어오면 플래그/카테고리에 맞게 포맷팅해서 Slack 채널로 전송

세션이 라이브로 진행 중일 때만 의미 있는 데이터가 들어온다 (연습/예선/레이스).

## 설정

`.env` 파일을 프로젝트 루트에 만든다 (`.env.example` 참고):

```
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0XXXXXXXXX
```

- `SLACK_BOT_TOKEN` — 메시지 전송에 사용 (`chat:write` 스코프 필요)
- `SLACK_CHANNEL_ID` — 알림 보낼 채널 ID
- `SLACK_APP_TOKEN` — Socket Mode용 (현재 단방향 전송이라 필수는 아니지만 향후 확장용으로 보관)

## 실행

```bash
pip install -r requirements.txt
python -m app.main
```

## Docker

```bash
docker build -t slack-f1 .
docker run --env-file .env slack-f1
```
