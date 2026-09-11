"""supervisor 동작 테스트 (네트워크 없이 가짜 클라이언트/시계로 검증)."""
import unittest

from app.session import SessionInfo
from app.supervisor import Supervisor, SupervisorConfig, SupervisorHooks


class FakeClient:
    def __init__(self) -> None:
        self.connected = False
        self.fail = False
        self.connect_calls = 0
        self.close_calls = 0
        self.last_message_at = 0.0

    def connect(self, timeout=None):
        self.connect_calls += 1
        if self.fail:
            self.connected = False
            return False
        self.connected = True
        return True

    def close(self):
        self.close_calls += 1
        self.connected = False


class FakeSource:
    def __init__(self, session=None) -> None:
        self.session = session
        self.polls = 0

    def poll(self):
        self.polls += 1
        return self.session


class Clock:
    def __init__(self, start=1000.0) -> None:
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def session(key, status="Started", name="Practice 1", meeting="Spanish Grand Prix"):
    return SessionInfo(key=key, meeting=meeting, name=name, status=status)


def build(source_session=None, **config):
    client = FakeClient()
    source = FakeSource(source_session)
    clock = Clock()
    events = {"change": [], "status": [], "connected": []}
    hooks = SupervisorHooks(
        on_session_change=lambda p, c: events["change"].append((p, c)),
        on_session_status=lambda p, c: events["status"].append((p, c)),
        on_connected=lambda first: events["connected"].append(first),
    )
    sup = Supervisor(
        client,
        source,
        hooks=hooks,
        config=SupervisorConfig(**config),
        clock=clock,
    )
    return sup, client, source, clock, events


class SupervisorTest(unittest.TestCase):
    def test_connects_and_detects_session(self):
        sup, client, _source, _clock, events = build(session(1))
        sup.tick()
        self.assertTrue(client.connected)
        self.assertEqual(1, client.connect_calls)
        self.assertEqual(1, len(events["change"]))
        self.assertIsNone(events["change"][0][0])
        self.assertEqual([True], events["connected"])

    def test_retries_forever_with_backoff(self):
        """예전 버그 회귀: 재연결 인터벌 목록을 다 쓰면 영원히 죽었다."""
        sup, client, _source, clock, _events = build()
        client.fail = True
        for _ in range(20):
            sup.tick()
            clock.advance(120)
        self.assertGreaterEqual(client.connect_calls, 20)

        client.fail = False
        sup.tick()
        self.assertTrue(client.connected)

    def test_backoff_waits_between_attempts(self):
        sup, client, _source, clock, _events = build()
        client.fail = True
        sup.tick()
        self.assertEqual(1, client.connect_calls)
        sup.tick()
        self.assertEqual(1, client.connect_calls)
        clock.advance(2)
        sup.tick()
        self.assertEqual(2, client.connect_calls)

    def test_new_session_resets_and_reconnects(self):
        sup, client, source, clock, events = build(session(1))
        sup.tick()
        self.assertEqual(1, client.connect_calls)

        clock.advance(120)
        source.session = session(2, name="Practice 2")
        sup.tick()

        self.assertEqual(2, len(events["change"]))
        self.assertEqual(1, events["change"][1][0].key)
        self.assertEqual(2, events["change"][1][1].key)
        self.assertGreaterEqual(client.close_calls, 1)
        self.assertEqual(2, client.connect_calls)
        self.assertTrue(client.connected)

    def test_status_change_is_reported_without_reconnect(self):
        sup, client, source, clock, events = build(session(1, status="Inactive"))
        sup.tick()
        connects = client.connect_calls

        clock.advance(120)
        source.session = session(1, status="Started")
        sup.tick()

        self.assertEqual(1, len(events["change"]))
        self.assertEqual(1, len(events["status"]))
        self.assertEqual("Started", events["status"][0][1].status)
        self.assertEqual(connects, client.connect_calls)

    def test_silence_during_running_session_reconnects(self):
        sup, client, _source, clock, _events = build(
            session(1, status="Started"), silence_timeout=100.0
        )
        sup.tick()
        client.last_message_at = clock()

        clock.advance(50)
        sup.tick()
        self.assertEqual(1, client.connect_calls)

        clock.advance(120)
        sup.tick()
        self.assertEqual(2, client.connect_calls)
        self.assertGreaterEqual(client.close_calls, 1)

    def test_no_silence_reconnect_when_session_not_running(self):
        sup, client, _source, clock, _events = build(
            session(1, status="Finalised"),
            silence_timeout=100.0,
            max_connection_age=10000.0,
        )
        sup.tick()
        clock.advance(5000)
        sup.tick()
        self.assertEqual(1, client.connect_calls)

    def test_idle_connection_is_recycled(self):
        sup, client, _source, clock, _events = build(
            session(1, status="Finalised"), max_connection_age=600.0
        )
        sup.tick()
        clock.advance(700)
        sup.tick()
        self.assertEqual(2, client.connect_calls)

    def test_feed_session_without_status_keeps_previous(self):
        sup, _client, _source, _clock, events = build(session(1, status="Started"))
        sup.tick()
        changed = sup.observe_session(
            SessionInfo(key=1, meeting="Spanish Grand Prix", name="Practice 1"),
            "feed",
        )
        self.assertFalse(changed)
        self.assertEqual("Started", sup.session.status)
        self.assertEqual([], events["status"])

    def test_feed_can_announce_new_session_before_static_poll(self):
        sup, client, _source, _clock, events = build(session(1))
        sup.tick()
        changed = sup.observe_session(session(2, name="Qualifying"), "feed")
        self.assertTrue(changed)
        self.assertEqual(2, len(events["change"]))
        self.assertEqual(1, client.connect_calls)

    def test_tick_survives_source_failure(self):
        sup, client, source, _clock, _events = build(session(1))

        def boom():
            raise RuntimeError("network down")

        source.poll = boom
        sup.tick()
        self.assertEqual(0, client.connect_calls)
        source.poll = lambda: session(1)
        sup.tick()
        self.assertTrue(client.connected)


if __name__ == "__main__":
    unittest.main()
