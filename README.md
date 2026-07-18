# py-vault-multicast

A small Python module for service discovery over UDP multicast on a local network: a publisher that periodically broadcasts a message, and a listener that receives such messages. Plus an optional PyQt6 widget that displays discovered services in a list.

## What it does

- `VaultMultiPublisher` sends a (freely chosen) message via multicast at regular intervals.
- `VaultMultiListener` listens on the multicast group, parses received messages as JSON, and delivers them via a callback and/or a psygnal signal.
- Both keep simple counters (packets/bytes sent or received, errors, uptime).
- `VaultServiceDiscovery` is a PyQt6 widget that wraps the listener and shows discovered services as a list with a timeout.

This is intentionally simple – not a replacement for mDNS/Zeroconf, no encryption, no deduplication across multiple network interfaces. Meant for simple "who's reachable on the network" scenarios on your own LAN.

## Installation

```bash
pip install .

# with the optional PyQt6 widget
pip install ".[gui]"

# with dev tools (PyQt6, pytest, mypy, ruff)
pip install ".[dev]"
```

### Requirements

- Python 3.10+
- psygnal >= 0.10
- PyQt6 >= 6.5 (only for the GUI widget, optional)

## Example: Publisher

```python
import vault_multicast
import json
import time

service_info = {
    "type": "VaultLibrary",
    "name": "My Service",
    "addr": "192.168.1.100:8080",
}

publisher = vault_multicast.VaultMultiPublisher(
    message=json.dumps(service_info),
    timeout=2.0,  # send interval in seconds
)
publisher.start()

time.sleep(10)
print(publisher.get_metrics())

publisher.stop()
```

Note: after `start()`, the publisher waits 5 seconds before sending its first message.

## Example: Listener

```python
import vault_multicast

def on_service_found(service_data):
    print(f"Found service: {service_data}")

listener = vault_multicast.VaultMultiListener(callback=on_service_found)
listener.start()

# alternatively, via the psygnal signal instead of a callback:
listener.recv_signal.connect(on_service_found)

listener.stop()
```

Also usable as a context manager (`with vault_multicast.VaultMultiListener() as listener: ...`), which calls `start()`/`stop()` automatically.

## GUI widget

```python
import sys
from PyQt6.QtWidgets import QApplication
from vault_multicast_service_discovery import VaultServiceDiscovery

app = QApplication(sys.argv)

discovery = VaultServiceDiscovery(type_filter="VaultLibrary")
discovery.return_signal.connect(lambda data: print("Selected:", data))
discovery.show()

sys.exit(app.exec())
```

The widget shows a list of discovered services (timed out after 30 seconds without a new message), a "Refresh" button, and a small metrics display. Messages from the listener thread are buffered in a queue and processed on the GUI thread via a timer – no Qt widgets are touched directly from the background thread.

## API

### VaultMultiPublisher

```python
VaultMultiPublisher(
    group: str = "224.1.1.1",
    port: int = 5004,
    ttl: int = 2,
    timeout: float = 2.0,      # seconds between broadcasts
    message: str = "there is nothing to see",
)
```

- `start()` / `stop(timeout=5.0)`
- `update_message(message)` or the `message` property (thread-safe)
- `get_metrics()` / `reset_metrics()`
- `is_running()`
- On a send error, the socket is rebuilt and sending continues; if that also fails, the send loop aborts.

### VaultMultiListener

```python
VaultMultiListener(
    group: str = "224.1.1.1",
    port: int = 5004,
    timeout: float = 2.0,      # socket timeout, determines polling interval
    buffer_size: int = 1400,
    callback: Callable[[dict], None] | None = None,
)
```

- `start()` / `stop(timeout=5.0)`
- `get_metrics()` / `reset_metrics()`
- `is_running()`
- `recv_signal` (psygnal `Signal(dict)`) – emitted for every received, valid JSON message
- Invalid JSON or UTF-8 is dropped and counted as an error; a socket error outside of the `stop()` path ends the receive loop without an automatic retry.

### Metrics

```python
{
    "packets_sent": 150,        # publisher only
    "packets_received": 450,    # listener only
    "bytes_sent": 45000,        # publisher only
    "bytes_received": 135000,   # listener only
    "errors": 0,
    "active_services": 3,       # listener only, count of distinct "addr" values seen
    "uptime_seconds": 300.5,
    "packets_per_second": 1.5,
}
```

## Message format

Any JSON object is accepted; the widget (`VaultServiceDiscovery`) requires the fields `type` and `addr`, otherwise the message is dropped:

```json
{
    "type": "VaultLibrary",
    "name": "My Service",
    "addr": "192.168.1.100:8080",
    "version": "1.0.0"
}
```

## Configuration

```python
DEFAULT_MULTICAST_GROUP = "224.1.1.1"
DEFAULT_PORT = 5004
DEFAULT_TTL = 2
DEFAULT_TIMEOUT = 2.0
DEFAULT_BUFFER_SIZE = 1400
SERVICE_TIMEOUT_SECONDS = 30  # GUI widget only
```

Group, port, etc. can be overridden when constructing the publisher/listener (see the constructor signatures above).

## Thread safety

- Metrics and the publisher message are protected by locks.
- psygnal's `recv_signal` can be emitted from any thread, but connected slots run in the emitting thread, not automatically on the GUI thread. `VaultServiceDiscovery` handles this explicitly via a queue plus a GUI timer – if you connect your own Qt slots directly to `recv_signal`, you need to handle that yourself.

## Testing

```bash
python -m pytest tests/
```

The test suite covers both modules (as of this writing: ~40 tests across `test_vault_multicast.py` and `test_service_discovery.py`). CI (`.github/workflows/ci.yml`) runs on Python 3.10–3.12 with `ruff check`, `mypy`, and `pytest --cov`.

Manual smoke test:

```bash
python vault_multicast.py
python vault_multicast_service_discovery.py
```

## Troubleshooting

No services found:
1. Check the firewall (UDP, port 5004 by default)
2. Make sure all devices are on the same network/VLAN
3. Choose a TTL appropriate for the network topology (default 2)

High error rate:
1. Check the message format (must be valid JSON)
2. Make sure `buffer_size` is large enough (default 1400 bytes)

## What's missing

There are no benchmarks or reliable performance numbers for this project – earlier versions of this file had made-up figures, which have been removed. Measure it yourself if you need numbers.

## License

Apache License 2.0, Copyright 2025 ecki. See [LICENSE](LICENSE).

## Contributing

Pull requests are welcome.

```bash
git clone git@github.com:vaultecki/py-vault-multicast.git
cd py-vault-multicast
pip install ".[dev]"
python -m pytest tests/
```

## Changelog

### Version 2.1.0 (2026)
- Switched dependency management from `requirements.txt` to `pyproject.toml`
- Migrated from PySignal to [psygnal](https://github.com/pyapp-kit/psygnal); now requires Python 3.10+
- Fixed: `VaultServiceDiscovery` was touching Qt widgets directly from the listener thread; now goes through a queue + GUI timer
- Fixed: missing `threading` import in `vault_multicast_service_discovery.py`
- Fixed: `VaultMultiListener.reset_metrics()` did not reset `active_services`
- Removed a lock in `VaultServiceDiscovery` that became redundant as a result
- Added a pytest suite for both modules; `ruff` and `mypy --disallow-untyped-defs` run in CI

### Version 2.0.0 (2025)
- Added metrics (packets, bytes, errors, active services, uptime)
- Automatic service timeout in the GUI widget
- Various fixes to thread safety and error handling

### Version 1.0.0 (2025)
- Initial version: publisher, listener, PyQt6 widget

## Related projects

- [python-zeroconf](https://github.com/jstasiak/python-zeroconf) – mDNS/Zeroconf, considerably more mature for service discovery
- [psygnal](https://github.com/pyapp-kit/psygnal)
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)

## Contact

Issues via [GitHub](https://github.com/vaultecki/py-vault-multicast/issues).
