import json
import time

import pytest

import vault_multicast
from vault_multicast import (
    MulticastMetrics,
    StoppableWorker,
    VaultMultiListener,
    VaultMultiPublisher,
)


# ---------------------------------------------------------------------------
# MulticastMetrics
# ---------------------------------------------------------------------------

class TestMulticastMetrics:
    def test_defaults(self):
        m = MulticastMetrics()
        assert m.packets_sent == 0
        assert m.packets_received == 0
        assert m.bytes_sent == 0
        assert m.bytes_received == 0
        assert m.errors == 0
        assert m.active_services == 0

    def test_reset_clears_counters_but_keeps_active_services(self):
        m = MulticastMetrics()
        m.packets_sent = 5
        m.bytes_sent = 100
        m.errors = 2
        m.active_services = 3
        old_start = m.start_time

        time.sleep(0.01)
        m.reset()

        assert m.packets_sent == 0
        assert m.bytes_sent == 0
        assert m.errors == 0
        assert m.active_services == 3  # not touched by reset()
        assert m.start_time > old_start

    def test_packets_per_second_zero_uptime_is_zero(self):
        m = MulticastMetrics()
        m.packets_sent = 10
        m.uptime_seconds = lambda: 0.0
        assert m.packets_per_second() == 0

    def test_packets_per_second_computes_rate(self):
        m = MulticastMetrics()
        m.packets_sent = 10
        m.packets_received = 10
        m.uptime_seconds = lambda: 4.0
        assert m.packets_per_second() == 5.0

    def test_to_dict_contains_expected_keys(self):
        m = MulticastMetrics()
        d = m.to_dict()
        assert set(d.keys()) == {
            "packets_sent",
            "packets_received",
            "bytes_sent",
            "bytes_received",
            "errors",
            "active_services",
            "uptime_seconds",
            "packets_per_second",
        }


# ---------------------------------------------------------------------------
# StoppableWorker
# ---------------------------------------------------------------------------

class _DummyWorker(StoppableWorker):
    def __init__(self):
        super().__init__()
        self.cleaned_up = False
        self.loops = 0

    def run(self):
        while not self._stop_event.is_set():
            self.loops += 1
            self._stop_event.wait(0.01)

    def _cleanup_resources(self):
        self.cleaned_up = True


class TestStoppableWorker:
    def test_base_run_raises_not_implemented(self):
        worker = StoppableWorker()
        with pytest.raises(NotImplementedError):
            worker.run()

    def test_start_stop_lifecycle(self):
        worker = _DummyWorker()
        assert not worker.is_running()

        worker.start()
        try:
            assert worker.is_running()
            time.sleep(0.05)
            assert worker.loops > 0
        finally:
            worker.stop()

        assert not worker.is_running()
        assert worker.cleaned_up

    def test_context_manager_starts_and_stops(self):
        with _DummyWorker() as worker:
            assert worker.is_running()
        assert not worker.is_running()
        assert worker.cleaned_up

    def test_start_is_idempotent_while_running(self):
        worker = _DummyWorker()
        worker.start()
        try:
            first_thread = worker._thread
            worker.start()
            assert worker._thread is first_thread
        finally:
            worker.stop()


# ---------------------------------------------------------------------------
# Fakes for socket-level publisher/listener tests
# ---------------------------------------------------------------------------

class FakePublisherSocket:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.fail_next = False

    def setsockopt(self, *args, **kwargs):
        pass

    def sendto(self, data, addr):
        if self.fail_next:
            self.fail_next = False
            raise OSError("simulated send failure")
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


class FakeListenerSocket:
    def __init__(self):
        import queue

        self._queue = queue.Queue()
        self.closed = False

    def push(self, data, addr=("127.0.0.1", 5004)):
        self._queue.put((data, addr))

    def setsockopt(self, *args, **kwargs):
        pass

    def bind(self, *args, **kwargs):
        pass

    def settimeout(self, *args, **kwargs):
        pass

    def recvfrom(self, bufsize):
        import queue
        import socket as socket_module

        try:
            return self._queue.get(timeout=0.05)
        except queue.Empty:
            raise socket_module.timeout()

    def close(self):
        self.closed = True


def _make_fast(worker):
    """Bypass the real sleep-based waits so tests run quickly."""
    worker._stop_event.wait = lambda timeout=None: worker._stop_event.is_set()
    return worker


# ---------------------------------------------------------------------------
# VaultMultiPublisher
# ---------------------------------------------------------------------------

class TestVaultMultiPublisher:
    def test_sends_current_message_and_updates_metrics(self, monkeypatch):
        created = []

        def fake_factory(*args, **kwargs):
            s = FakePublisherSocket()
            created.append(s)
            return s

        monkeypatch.setattr(vault_multicast.socket, "socket", fake_factory)

        publisher = VaultMultiPublisher(message="hello", timeout=0.01)
        _make_fast(publisher)
        publisher.start()
        try:
            time.sleep(0.05)
        finally:
            publisher.stop()

        assert len(created) == 1
        sock = created[0]
        assert sock.closed
        assert len(sock.sent) > 0
        data, addr = sock.sent[0]
        assert data == b"hello"
        assert addr == (publisher.group, publisher.port)

        metrics = publisher.get_metrics()
        assert metrics["packets_sent"] > 0
        assert metrics["bytes_sent"] > 0

    def test_message_property_is_updatable(self, monkeypatch):
        monkeypatch.setattr(
            vault_multicast.socket, "socket", lambda *a, **k: FakePublisherSocket()
        )
        publisher = VaultMultiPublisher(message="first")
        assert publisher.message == "first"

        publisher.update_message("second")
        assert publisher.message == "second"

        publisher.message = "third"
        assert publisher.message == "third"

    def test_send_error_increments_errors_and_reinitializes_socket(self, monkeypatch):
        created = []

        def fake_factory(*args, **kwargs):
            s = FakePublisherSocket()
            created.append(s)
            return s

        monkeypatch.setattr(vault_multicast.socket, "socket", fake_factory)
        monkeypatch.setattr(vault_multicast.time, "sleep", lambda s: None)

        publisher = VaultMultiPublisher(message="hello", timeout=0.01)
        _make_fast(publisher)
        created[0].fail_next = True

        publisher.start()
        try:
            time.sleep(0.05)
        finally:
            publisher.stop()

        assert len(created) >= 2  # socket was reinitialized after the error
        metrics = publisher.get_metrics()
        assert metrics["errors"] >= 1

    def test_reset_metrics(self, monkeypatch):
        monkeypatch.setattr(
            vault_multicast.socket, "socket", lambda *a, **k: FakePublisherSocket()
        )
        publisher = VaultMultiPublisher()
        publisher.metrics.packets_sent = 5
        publisher.reset_metrics()
        assert publisher.get_metrics()["packets_sent"] == 0


# ---------------------------------------------------------------------------
# VaultMultiListener
# ---------------------------------------------------------------------------

class TestVaultMultiListener:
    def test_receives_and_parses_json_message(self, monkeypatch):
        fake_sock = FakeListenerSocket()
        monkeypatch.setattr(vault_multicast.socket, "socket", lambda *a, **k: fake_sock)

        received = []
        listener = VaultMultiListener(callback=received.append)
        listener.start()
        try:
            payload = {"type": "svc", "addr": "10.0.0.1:1234"}
            fake_sock.push(json.dumps(payload).encode("utf-8"))
            time.sleep(0.2)
        finally:
            listener.stop()

        assert received == [payload]
        metrics = listener.get_metrics()
        assert metrics["packets_received"] == 1
        assert metrics["active_services"] == 1

    def test_invalid_json_increments_errors_without_crashing(self, monkeypatch):
        fake_sock = FakeListenerSocket()
        monkeypatch.setattr(vault_multicast.socket, "socket", lambda *a, **k: fake_sock)

        listener = VaultMultiListener()
        listener.start()
        try:
            fake_sock.push(b"not-json")
            time.sleep(0.2)
        finally:
            listener.stop()

        metrics = listener.get_metrics()
        assert metrics["errors"] == 1
        assert metrics["packets_received"] == 1

    def test_invalid_utf8_increments_errors_without_crashing(self, monkeypatch):
        fake_sock = FakeListenerSocket()
        monkeypatch.setattr(vault_multicast.socket, "socket", lambda *a, **k: fake_sock)

        listener = VaultMultiListener()
        listener.start()
        try:
            fake_sock.push(b"\xff\xfe\xfd")
            time.sleep(0.2)
        finally:
            listener.stop()

        metrics = listener.get_metrics()
        assert metrics["errors"] == 1

    def test_recv_signal_emitted(self, monkeypatch):
        fake_sock = FakeListenerSocket()
        monkeypatch.setattr(vault_multicast.socket, "socket", lambda *a, **k: fake_sock)

        # PySignal keeps only a weakref to connected slots, so the receiver
        # must be a live object (a bound method), not a throwaway callable.
        class Collector:
            def __init__(self):
                self.seen = []

            def receive(self, data):
                self.seen.append(data)

        collector = Collector()
        listener = VaultMultiListener()
        listener.recv_signal.connect(collector.receive)

        listener.start()
        try:
            payload = {"type": "svc", "addr": "10.0.0.2:1"}
            fake_sock.push(json.dumps(payload).encode("utf-8"))
            time.sleep(0.2)
        finally:
            listener.stop()

        assert collector.seen == [payload]

    def test_reset_metrics_clears_service_addresses(self, monkeypatch):
        fake_sock = FakeListenerSocket()
        monkeypatch.setattr(vault_multicast.socket, "socket", lambda *a, **k: fake_sock)

        listener = VaultMultiListener()
        listener.start()
        try:
            fake_sock.push(json.dumps({"type": "svc", "addr": "10.0.0.3:1"}).encode())
            time.sleep(0.2)
            assert listener.get_metrics()["active_services"] == 1
            listener.reset_metrics()
            assert listener.get_metrics()["active_services"] == 0
        finally:
            listener.stop()
