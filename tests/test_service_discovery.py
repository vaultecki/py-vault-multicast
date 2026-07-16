import threading
from datetime import datetime, timedelta

import pytest
from psygnal import Signal

import vault_multicast_service_discovery as vsd
from vault_multicast_service_discovery import ServiceEntry, VaultServiceDiscovery


# ---------------------------------------------------------------------------
# ServiceEntry
# ---------------------------------------------------------------------------

class TestServiceEntry:
    def test_is_alive_true_when_recent(self):
        entry = ServiceEntry(data={"addr": "1.2.3.4:1"}, last_seen=datetime.now())
        assert entry.is_alive

    def test_is_alive_false_when_stale(self):
        stale = datetime.now() - timedelta(seconds=vsd.SERVICE_TIMEOUT_SECONDS + 1)
        entry = ServiceEntry(data={"addr": "1.2.3.4:1"}, last_seen=stale)
        assert not entry.is_alive

    def test_addr_returns_field(self):
        entry = ServiceEntry(data={"addr": "1.2.3.4:1"}, last_seen=datetime.now())
        assert entry.addr == "1.2.3.4:1"

    def test_addr_defaults_to_unknown(self):
        entry = ServiceEntry(data={}, last_seen=datetime.now())
        assert entry.addr == "unknown"


# ---------------------------------------------------------------------------
# VaultServiceDiscovery
# ---------------------------------------------------------------------------

class FakeListener:
    """Stand-in for vault_multicast.VaultMultiListener that does no networking."""

    recv_signal = Signal(dict)
    instances = []

    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = False
        self.metrics_reset = False
        FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get_metrics(self):
        return {
            "packets_received": 0,
            "bytes_received": 0,
            "errors": 0,
            "uptime_seconds": 0.0,
            "packets_per_second": 0.0,
        }

    def reset_metrics(self):
        self.metrics_reset = True


@pytest.fixture(autouse=True)
def fake_listener(monkeypatch):
    FakeListener.instances = []
    monkeypatch.setattr(vsd.vault_multicast, "VaultMultiListener", FakeListener)
    yield
    FakeListener.instances = []


@pytest.fixture
def widget(qapp):
    w = VaultServiceDiscovery()
    yield w
    w.stop()


class TestVaultServiceDiscovery:
    def test_uses_fake_listener_and_starts_it(self, widget):
        assert len(FakeListener.instances) == 1
        assert FakeListener.instances[0].started

    def test_validate_requires_dict(self, widget):
        assert widget._validate_service_data("not-a-dict") is False

    def test_validate_requires_type_and_addr(self, widget):
        assert widget._validate_service_data({"type": "x"}) is False
        assert widget._validate_service_data({"addr": "1.2.3.4:1"}) is False
        assert widget._validate_service_data({"type": "x", "addr": "1.2.3.4:1"}) is True

    def test_validate_respects_type_filter(self, qapp):
        w = VaultServiceDiscovery(type_filter="Vault")
        try:
            assert w._validate_service_data({"type": "VaultLibrary", "addr": "a"}) is True
            assert w._validate_service_data({"type": "Other", "addr": "a"}) is False
        finally:
            w.stop()

    def test_on_service_discovered_adds_new_service(self, widget):
        data = {"type": "svc", "addr": "10.0.0.1:1", "name": "Foo"}
        widget._on_service_discovered(data)

        assert "10.0.0.1:1" in widget.services
        assert widget.tree.topLevelItemCount() == 1
        assert widget.tree.topLevelItem(0).text(0) == "Foo"

    def test_on_service_discovered_updates_existing_service(self, widget):
        data = {"type": "svc", "addr": "10.0.0.1:1", "name": "Foo"}
        widget._on_service_discovered(data)
        first_seen = widget.services["10.0.0.1:1"].last_seen

        updated = {"type": "svc", "addr": "10.0.0.1:1", "name": "Foo2"}
        widget._on_service_discovered(updated)

        assert len(widget.services) == 1
        assert widget.tree.topLevelItemCount() == 1
        assert widget.services["10.0.0.1:1"].last_seen >= first_seen
        assert widget.services["10.0.0.1:1"].data["name"] == "Foo2"

    def test_on_service_discovered_ignores_invalid_data(self, widget):
        widget._on_service_discovered({"type": "svc"})  # missing addr
        assert widget.services == {}
        assert widget.tree.topLevelItemCount() == 0

    def test_cleanup_removes_stale_services(self, widget):
        stale_addr = "10.0.0.9:1"
        widget._on_service_discovered({"type": "svc", "addr": stale_addr, "name": "Stale"})
        widget.services[stale_addr].last_seen = datetime.now() - timedelta(
            seconds=vsd.SERVICE_TIMEOUT_SECONDS + 1
        )

        widget._cleanup_old_services()

        assert stale_addr not in widget.services
        assert widget.tree.topLevelItemCount() == 0

    def test_refresh_clears_services_and_resets_metrics(self, widget):
        widget._on_service_discovered({"type": "svc", "addr": "10.0.0.1:1"})
        widget._refresh_services()

        assert widget.services == {}
        assert widget.tree.topLevelItemCount() == 0
        assert FakeListener.instances[0].metrics_reset

    def test_stop_stops_listener_and_timers(self, widget):
        widget.stop()
        assert FakeListener.instances[0].stopped
        assert not widget.cleanup_timer.isActive()
        assert not widget.metrics_timer.isActive()
        assert not widget.dispatch_timer.isActive()


class TestGuiThreadDispatch:
    """The listener emits recv_signal from its own background thread; Qt
    widgets must only be touched from the GUI thread. Verify that received
    data is queued rather than applied immediately, and only lands in
    widget.services once the GUI-thread dispatch timer processes it."""

    def test_emit_is_queued_not_applied_immediately(self, widget):
        data = {"type": "svc", "addr": "10.0.0.5:1", "name": "Queued"}
        FakeListener.instances[0].recv_signal.emit(data)

        assert widget.services == {}
        assert widget._pending_services.qsize() == 1

    def test_dispatch_timer_applies_queued_data(self, widget):
        data = {"type": "svc", "addr": "10.0.0.5:1", "name": "Queued"}
        FakeListener.instances[0].recv_signal.emit(data)

        widget._dispatch_pending_services()

        assert "10.0.0.5:1" in widget.services
        assert widget._pending_services.qsize() == 0

    def test_emit_from_background_thread_is_dispatched_safely(self, widget):
        data = {"type": "svc", "addr": "10.0.0.6:1", "name": "FromThread"}

        t = threading.Thread(target=FakeListener.instances[0].recv_signal.emit, args=(data,))
        t.start()
        t.join(timeout=2)

        widget._dispatch_pending_services()

        assert "10.0.0.6:1" in widget.services
