# Slack F1 Race Control Notifier

F1 라이브타이밍 SignalR 피드를 실시간으로 구독해서, Race Control Message(옜로 플래그, 세이프티카, 페널티, 조사 등)가 뜼면 Slack 채널로 알림을 보내는 봇.

한 번 띄워두면 다음 GP, 다음 세션까지 알아서 계속 물어온다.

## 동작 방식

1. F1 공식 라이브타이밍 SignalR Core 엔드포인트(`wss://livetiming.formula1.com/signalrcore`)에 연결
2. `RaceControlMessages` 외 토픽(TimingData/DriverList 등)을 구독
3. 새 메시지가 들어오면 플래그/카테고리에 맞게 포맷팅해서 Slack 채널로 전송
4. `supervisor` 가 연결과 세션을 계속 감시한다

### 세션·연결 감시 (`app/supervisor.py`)

- **새 세션 자동 감지** — 정적 `SessionInfo.json` 을 주기적으로 폴링하고, 연결되어 있으면 SignalR `SessionInfo` 푸시도 같이 본다. 세션 Key 가 바뀌면 순위·타이어·중복키 상태를 전부 리셋하고 새로 연결한다.
- **무한 재연결** — 1/2/5/10/20/30/60초 백오프로 계속 재시도한다(횟수 제한 없음). 재연결하면 항상 `Subscribe` 를 다시 보낸다.
- **좌비 연결 감지** — 세션이 진행 중인데 `SILENCE_TIMEOUT` 동안 데이터가 한 건도 안 오면 끊고 다시 붙는다.
- **유휴 연결 재활용** — 세션이 없는 동안에도 `MAX_CONNECTION_AGE` 마다 연결을 새로 맺는다(조용히 죽은 소켓 방지).

### 중복/과거 메시지

구독 직후에는 현재 세션의 Race Control 로그가 통째로 내려온다. `Utc + Message` 키로 중복을 거르고, 스냅샷에 섮인 메시지는 `SNAPSHOT_MAX_AGE`(기본 10분)보다 오래된 건만 버린다. 덕분에 재연결 중 놓친 메시지는 살리고, 지나간 세션 로그로 채널을 도배하진 않는다.

## 설정

`.env` 파일을 프로젝트 루트에 만든다 (`.env.example` 참고):

```
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0XXXXXXXXX
```

필수

| 변수 | 설명 |
| --- | --- |
| `SLACK_BOT_TOKEN` | 메시지 전송용 (`chat:write` 스코프 필요) |
| `SLACK_CHANNEL_ID` | 알림 보낼 채널 ID |
| `SLACK_APP_TOKEN` | Socket Mode용 (현재 단방향 전송이라 필수는 아니지만 향후 확장용으로 보관) |

선택 (기본값)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NOTIFY_SNAPSHOT` | `false` | 구독 직후 스냅샷을 나이 상관없이 전부 전송 |
| `SNAPSHOT_MAX_AGE` | `600` | 스냅샷에서 이 나이(초)보다 오래된 메시지는 생략 |
| `ANNOUNCE_SESSION` | `true` | 세션 감시 시작/시작·종료를 Slack에 알림 |
| `TICK_INTERVAL` | `5` | 감시 루프 주기(초) |
| `SESSION_POLL_INTERVAL` | `60` | 현재 세션 확인 주기(초) |
| `SESSION_POLL_INTERVAL_LIVE` | `30` | 세션 진행 중 확인 주기(초) |
| `SILENCE_TIMEOUT` | `180` | 진행 중 무응답 판정 시간(초) |
| `MAX_CONNECTION_AGE` | `3600` | 비세션 구간 연결 재활용 주기(초) |
| `HEALTH_FILE` | `/tmp/f1-bot-alive` | 살아있음 표시 파일(도커 HEALTHCHECK용). 비우면 비활성 |

## 실행

```bash
pip install -r requirements.txt
python -m app.main
```

세션이 없는 시간에도 그냥 띄워두면 된다. 다음 세션이 올라오면 알아서 잡는다.

## 테스트

```bash
python -m unittest discover -s tests -t .
```

네트워크 없이 돌아간다 (가짜 클라이언트/시계 사용).

## Docker

```bash
docker build -t slack-f1 .
docker run -d --restart unless-stopped --env-file .env slack-f1
```
