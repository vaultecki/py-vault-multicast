# Copyright [2025] [ecki]
# SPDX-License-Identifier: Apache-2.0

import json
import sys
import logging
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

import PyQt6.QtWidgets
import PyQt6.QtCore
import PySignal
import libs.multicast.vault_multicast as vault_multicast

logger = logging.getLogger(__name__)

# Constants
SERVICE_TIMEOUT_SECONDS = 30


@dataclass
class ServiceEntry:
    """Represents a discovered service with metadata."""
    data: Dict[str, Any]
    last_seen: datetime
    tree_item: Optional[PyQt6.QtWidgets.QTreeWidgetItem] = None

    @property
    def is_alive(self) -> bool:
        """Check if service is still active."""
        return datetime.now() - self.last_seen < timedelta(seconds=SERVICE_TIMEOUT_SECONDS)

    @property
    def addr(self) -> str:
        """Get service address."""
        return self.data.get("addr", "unknown")


class VaultServiceDiscovery(PyQt6.QtWidgets.QWidget):
    """Widget for displaying and selecting discovered multicast services.

    Features:
    - Service timeout (automatically removes old entries after 30 seconds)
    - Thread-safe service management
    - Real-time metrics display
    - Data validation
    - Manual refresh capability
    """

    return_signal = PySignal.ClassSignal()

    def __init__(self, type_filter: str = ""):
        super().__init__()

        self.type_filter = type_filter
        self.services: Dict[str, ServiceEntry] = {}
        self._lock = threading.Lock()
        self._is_running = True

        self._setup_ui()
        self._start_listener()
        self._start_cleanup_timer()
        self._start_metrics_timer()

    def _setup_ui(self):
        """Initialize user interface."""
        main_layout = PyQt6.QtWidgets.QVBoxLayout()

        # Service tree
        self.tree = PyQt6.QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Service", "IP / Port", "Last Seen"])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 150)

        # Metrics display
        metrics_group = PyQt6.QtWidgets.QGroupBox("Metrics")
        metrics_layout = PyQt6.QtWidgets.QGridLayout()

        self.lbl_active_services = PyQt6.QtWidgets.QLabel("Active Services: 0")
        self.lbl_packets_received = PyQt6.QtWidgets.QLabel("Packets Received: 0")
        self.lbl_bytes_received = PyQt6.QtWidgets.QLabel("Bytes Received: 0")
        self.lbl_errors = PyQt6.QtWidgets.QLabel("Errors: 0")
        self.lbl_uptime = PyQt6.QtWidgets.QLabel("Uptime: 0s")
        self.lbl_rate = PyQt6.QtWidgets.QLabel("Rate: 0.0 pkt/s")

        metrics_layout.addWidget(self.lbl_active_services, 0, 0)
        metrics_layout.addWidget(self.lbl_packets_received, 0, 1)
        metrics_layout.addWidget(self.lbl_bytes_received, 1, 0)
        metrics_layout.addWidget(self.lbl_errors, 1, 1)
        metrics_layout.addWidget(self.lbl_uptime, 2, 0)
        metrics_layout.addWidget(self.lbl_rate, 2, 1)

        metrics_group.setLayout(metrics_layout)

        # Buttons
        button_layout = PyQt6.QtWidgets.QHBoxLayout()

        btn_connect = PyQt6.QtWidgets.QPushButton("Connect")
        btn_connect.clicked.connect(self._on_connect_clicked)

        btn_refresh = PyQt6.QtWidgets.QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_services)

        btn_reset_metrics = PyQt6.QtWidgets.QPushButton("Reset Metrics")
        btn_reset_metrics.clicked.connect(self._reset_metrics)

        button_layout.addWidget(btn_connect)
        button_layout.addWidget(btn_refresh)
        button_layout.addWidget(btn_reset_metrics)

        # Add all to main layout
        main_layout.addWidget(self.tree)
        main_layout.addWidget(metrics_group)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)
        self.setMinimumWidth(600)
        self.setWindowTitle("Vault Service Discovery")

    def _start_listener(self):
        """Start multicast listener."""
        try:
            self.listener = vault_multicast.VaultMultiListener()
            self.listener.recv_signal.connect(self._on_service_discovered)
            self.listener.start()
            logger.info("Multicast listener started")
        except Exception as e:
            logger.error(f"Failed to start listener: {e}", exc_info=True)
            PyQt6.QtWidgets.QMessageBox.critical(
                self, "Error", f"Failed to start service discovery: {e}"
            )

    def _start_cleanup_timer(self):
        """Start timer for automatic cleanup of old services."""
        self.cleanup_timer = PyQt6.QtCore.QTimer()
        self.cleanup_timer.timeout.connect(self._cleanup_old_services)
        self.cleanup_timer.start(5000)  # Every 5 seconds

    def _start_metrics_timer(self):
        """Start timer for metrics display updates."""
        self.metrics_timer = PyQt6.QtCore.QTimer()
        self.metrics_timer.timeout.connect(self._update_metrics_display)
        self.metrics_timer.start(1000)  # Every second

    def _update_metrics_display(self):
        """Update metrics labels with current values."""
        if hasattr(self, 'listener'):
            metrics = self.listener.get_metrics()

            # Update active services count from our tracked services
            with self._lock:
                active_count = sum(1 for s in self.services.values() if s.is_alive)

            self.lbl_active_services.setText(f"Active Services: {active_count}")
            self.lbl_packets_received.setText(f"Packets Received: {metrics['packets_received']}")
            self.lbl_bytes_received.setText(
                f"Bytes Received: {metrics['bytes_received']:,} ({metrics['bytes_received'] / 1024:.1f} KB)"
            )
            self.lbl_errors.setText(f"Errors: {metrics['errors']}")
            self.lbl_uptime.setText(f"Uptime: {metrics['uptime_seconds']:.0f}s")
            self.lbl_rate.setText(f"Rate: {metrics['packets_per_second']:.2f} pkt/s")

    def _validate_service_data(self, data: Dict[str, Any]) -> bool:
        """Validate structure of received service data.

        Args:
            data: Received JSON data

        Returns:
            True if data is valid
        """
        if not isinstance(data, dict):
            logger.warning("Received non-dict data")
            return False

        required_fields = ["type", "addr"]
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field: {field}")
                return False

        # Check type filter
        if self.type_filter and self.type_filter not in data.get("type", ""):
            return False

        return True

    def _on_service_discovered(self, data: Dict[str, Any]):
        """Callback when service is discovered.

        Args:
            data: Service information as dictionary
        """
        if not self._validate_service_data(data):
            return

        addr = data.get("addr")

        with self._lock:
            if addr in self.services:
                # Update existing service
                self.services[addr].last_seen = datetime.now()
                self.services[addr].data = data
                self._update_tree_item(self.services[addr])
            else:
                # Add new service
                service = ServiceEntry(data=data, last_seen=datetime.now())
                self.services[addr] = service
                self._add_tree_item(service)

        logger.info(f"Service discovered: {data.get('name', 'unknown')} at {addr}")

    def _add_tree_item(self, service: ServiceEntry):
        """Add service to tree widget."""
        item = PyQt6.QtWidgets.QTreeWidgetItem(self.tree)
        item.setText(0, service.data.get("name", "Unknown Service"))
        item.setText(1, service.addr)
        item.setText(2, service.last_seen.strftime("%H:%M:%S"))
        item.setData(0, PyQt6.QtCore.Qt.ItemDataRole.UserRole, service.addr)
        service.tree_item = item

    def _update_tree_item(self, service: ServiceEntry):
        """Update existing tree entry."""
        if service.tree_item:
            service.tree_item.setText(2, service.last_seen.strftime("%H:%M:%S"))

    def _cleanup_old_services(self):
        """Remove services not seen for SERVICE_TIMEOUT_SECONDS."""
        with self._lock:
            to_remove = []
            for addr, service in self.services.items():
                if not service.is_alive:
                    to_remove.append(addr)
                    if service.tree_item:
                        index = self.tree.indexOfTopLevelItem(service.tree_item)
                        if index >= 0:
                            self.tree.takeTopLevelItem(index)

            for addr in to_remove:
                logger.info(f"Service timeout: {addr}")
                del self.services[addr]

    def _refresh_services(self):
        """Manual refresh - removes all services for new search."""
        with self._lock:
            self.services.clear()
            self.tree.clear()

        if hasattr(self, 'listener'):
            self.listener.reset_metrics()

        logger.info("Service list cleared for refresh")

    def _reset_metrics(self):
        """Reset all metrics counters."""
        if hasattr(self, 'listener'):
            self.listener.reset_metrics()
            logger.info("Metrics reset")

    def _on_connect_clicked(self):
        """Handler for connect button."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            PyQt6.QtWidgets.QMessageBox.warning(
                self, "No Selection", "Please select a service to connect to."
            )
            return

        # Get service data
        addr = selected_items[0].data(0, PyQt6.QtCore.Qt.ItemDataRole.UserRole)

        with self._lock:
            if addr in self.services:
                service_data = self.services[addr].data
                self.return_signal.emit(service_data)
                logger.info(f"Connecting to service: {addr}")
                self.stop()
                self.hide()

    def stop(self):
        """Stop listener and timers."""
        self._is_running = False

        if hasattr(self, 'cleanup_timer'):
            self.cleanup_timer.stop()

        if hasattr(self, 'metrics_timer'):
            self.metrics_timer.stop()

        if hasattr(self, 'listener'):
            try:
                self.listener.recv_signal.disconnect(self._on_service_discovered)
                self.listener.stop()
                logger.info("Listener stopped")
            except Exception as e:
                logger.error(f"Error stopping listener: {e}")

    def closeEvent(self, event):
        """Override for clean shutdown."""
        self.stop()
        super().closeEvent(event)


class TestMainWindow(PyQt6.QtWidgets.QMainWindow):
    """Example main window for testing the service discovery."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Service Discovery Test")
        self.bsd = None
        self.bsd_data = None

        # Main widget
        central_widget = PyQt6.QtWidgets.QWidget()
        layout = PyQt6.QtWidgets.QVBoxLayout()

        # Button to show discovery
        self.button = PyQt6.QtWidgets.QPushButton("Show Service Discovery")
        self.button.clicked.connect(self.show_bsd_widget)

        # Label to show selected service
        self.lbl_result = PyQt6.QtWidgets.QLabel("No service selected")
        self.lbl_result.setWordWrap(True)

        layout.addWidget(self.button)
        layout.addWidget(self.lbl_result)

        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        # Timer for checking if discovery window was closed
        self.check_timer = PyQt6.QtCore.QTimer()
        self.check_timer.timeout.connect(self._check_bsd_widget)
        self.check_timer.start(500)

    def show_bsd_widget(self):
        """Show the service discovery widget."""
        logger.info("Opening service discovery")
        self.bsd = VaultServiceDiscovery(type_filter="VaultLibrary")
        self.bsd.return_signal.connect(self.on_submit_value)
        self.bsd.show()

    def _check_bsd_widget(self):
        """Check if discovery widget was closed."""
        if self.bsd:
            if not self.bsd.isVisible():
                self.bsd.return_signal.disconnect(self.on_submit_value)
                self.bsd.stop()
                self.bsd = None

    def on_submit_value(self, value: Dict[str, Any]):
        """Handle selected service data."""
        logger.info(f"Service selected: {value}")
        self.bsd_data = value

        # Display in label
        result_text = f"<b>Selected Service:</b><br>"
        result_text += f"Name: {value.get('name', 'Unknown')}<br>"
        result_text += f"Address: {value.get('addr', 'Unknown')}<br>"
        result_text += f"Type: {value.get('type', 'Unknown')}"

        self.lbl_result.setText(result_text)

    def closeEvent(self, event):
        """Clean shutdown."""
        self.check_timer.stop()
        if self.bsd:
            self.bsd.stop()
        super().closeEvent(event)


class TestMainApp:
    """Test application wrapper."""

    def __init__(self):
        self.app = PyQt6.QtWidgets.QApplication(sys.argv)
        self.app.setStyle('Fusion')
        self.tmw = TestMainWindow()

    def run(self):
        """Run the application."""
        self.tmw.show()
        sys.exit(self.app.exec())


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run test application
    multi_window = TestMainApp()
    multi_window.run()
    