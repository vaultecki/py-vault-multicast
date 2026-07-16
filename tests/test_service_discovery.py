import threading
from datetime import datetime, timedelta
from typing import ClassVar

import PyQt6.QtCore
import PyQt6.QtWidgets
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
    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = False
        self.metrics_reset = False
        self.metrics_data = {
            "packets_received": 0,
            "bytes_received": 0,
            "errors": 0,
            "uptime_seconds": 0.0,
            "packets_per_second": 0.0,
        }
        FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get_metrics(self):
        return self.metrics_data

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


class TestUpdateMetricsDisplay:
    def test_updates_labels_from_listener_metrics(self, widget):
        FakeListener.instances[0].metrics_data = {
            "packets_received": 42,
            "bytes_received": 2048,
            "errors": 3,
            "uptime_seconds": 12.5,
            "packets_per_second": 1.75,
        }

        widget._update_metrics_display()

        assert widget.lbl_packets_received.text() == "Packets Received: 42"
        assert widget.lbl_bytes_received.text() == "Bytes Received: 2,048 (2.0 KB)"
        assert widget.lbl_errors.text() == "Errors: 3"
        assert widget.lbl_uptime.text() == "Uptime: 12s"
        assert widget.lbl_rate.text() == "Rate: 1.75 pkt/s"

    def test_active_services_counts_only_alive_entries(self, widget):
        widget._on_service_discovered({"type": "svc", "addr": "10.0.0.7:1"})
        widget._on_service_discovered({"type": "svc", "addr": "10.0.0.8:1"})
        widget.services["10.0.0.8:1"].last_seen = datetime.now() - timedelta(
            seconds=vsd.SERVICE_TIMEOUT_SECONDS + 1
        )

        widget._update_metrics_display()

        assert widget.lbl_active_services.text() == "Active Services: 1"


class TestOnConnectClicked:
    def test_no_selection_shows_warning(self, widget, monkeypatch):
        calls = []
        monkeypatch.setattr(
            PyQt6.QtWidgets.QMessageBox, "warning", lambda *a, **k: calls.append((a, k))
        )

        widget._on_connect_clicked()

        assert len(calls) == 1

    def test_selection_not_in_services_does_nothing(self, widget):
        item = PyQt6.QtWidgets.QTreeWidgetItem(widget.tree)
        item.setData(0, PyQt6.QtCore.Qt.ItemDataRole.UserRole, "not-tracked")
        item.setSelected(True)

        seen = []
        widget.return_signal.connect(seen.append)

        widget._on_connect_clicked()

        assert seen == []
        assert not FakeListener.instances[0].stopped

    def test_selection_emits_return_signal_and_stops(self, widget):
        data = {"type": "svc", "addr": "10.0.0.9:1", "name": "Foo"}
        widget._on_service_discovered(data)
        widget.tree.topLevelItem(0).setSelected(True)

        seen = []
        widget.return_signal.connect(seen.append)

        widget._on_connect_clicked()

        assert seen == [data]
        assert FakeListener.instances[0].stopped
