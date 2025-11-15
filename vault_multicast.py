import logging
import json
import socket
import struct
import threading
import time
from typing import Optional, Callable
import PySignal

logger = logging.getLogger(__name__)

# Konstanten
DEFAULT_MULTICAST_GROUP = "224.1.1.1"
DEFAULT_PORT = 5004
DEFAULT_TTL = 2
DEFAULT_TIMEOUT = 2.0
DEFAULT_BUFFER_SIZE = 1400


class StoppableWorker:
    """Base class for threads that can be cleanly started and stopped."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Creates and starts the thread."""
        if self._thread is None or not self._thread.is_alive():
            logger.debug(f"{self.__class__.__name__} starting thread.")
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_wrapper)
            self._thread.daemon = True
            self._thread.start()

    def _run_wrapper(self) -> None:
        """Wrapper for run() with error handling."""
        try:
            self.run()
        except Exception as e:
            logger.error(f"{self.__class__.__name__} thread error: {e}", exc_info=True)
        finally:
            logger.debug(f"{self.__class__.__name__} thread finished.")

    def stop(self, timeout: float = 5.0) -> None:
        """Sets the event and waits for the thread to terminate.

        Args:
            timeout: Maximum wait time in seconds for thread termination
        """
        logger.debug(f"{self.__class__.__name__} stopping.")
        self._stop_event.set()

        # Subclasses should close specific resources
        self._cleanup_resources()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(f"{self.__class__.__name__} thread did not stop within {timeout}s")

    def _cleanup_resources(self) -> None:
        """Hook for subclasses to clean up resources."""
        pass

    def run(self) -> None:
        """The main work loop of the thread. MUST be overridden in subclass."""
        raise NotImplementedError("The method 'run()' must be implemented in the subclass.")

    def is_running(self) -> bool:
        """Checks if the thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self):
        """Context Manager Support."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context Manager Support."""
        self.stop()
        return False


class VaultMultiPublisher(StoppableWorker):
    """Multicast Publisher for Vault messages."""

    def __init__(
            self,
            group: str = DEFAULT_MULTICAST_GROUP,
            port: int = DEFAULT_PORT,
            ttl: int = DEFAULT_TTL,
            timeout: float = DEFAULT_TIMEOUT,
            message: str = "there is nothing to see"
    ):
        """Initializes the Multicast Publisher.

        Args:
            group: Multicast group address
            port: UDP port
            ttl: Time-To-Live for multicast packets
            timeout: Wait time between messages (seconds)
            message: Message to send
        """
        super().__init__()
        self.group = group
        self.port = port
        self.ttl = ttl
        self.timeout = timeout
        self._message = message
        self._message_lock = threading.Lock()

        # Initialize socket
        self._sock: Optional[socket.socket] = None
        self._init_socket()

    def _init_socket(self) -> None:
        """Initializes the multicast socket."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.ttl)
            logger.debug(f"VaMuPu socket initialized for {self.group}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to initialize socket: {e}")
            raise

    def _cleanup_resources(self) -> None:
        """Closes the socket."""
        if self._sock:
            try:
                self._sock.close()
                logger.debug("VaMuPu socket closed.")
            except Exception as e:
                logger.error(f"Error closing socket: {e}")
            finally:
                self._sock = None

    def run(self) -> None:
        """Main loop for sending multicast messages."""
        logger.info(f"VaMuPu starting advertisement loop to {self.group}:{self.port}")

        # Initial delay (5 seconds)
        if self._stop_event.wait(5):
            return

        while not self._stop_event.is_set():
            try:
                # Thread-safe access to message
                with self._message_lock:
                    current_message = self._message

                # Send message
                self._sock.sendto(current_message.encode("utf-8"), (self.group, self.port))
                logger.debug(f"VaMuPu published: {current_message[:100]}...")

            except Exception as e:
                logger.error(f"Error sending multicast: {e}")
                # Try to reinitialize socket on error
                if not self._stop_event.is_set():
                    try:
                        self._cleanup_resources()
                        time.sleep(1)
                        self._init_socket()
                    except Exception as reinit_error:
                        logger.error(f"Failed to reinitialize socket: {reinit_error}")
                        break

            # Wait for 'timeout' seconds OR until event is set
            self._stop_event.wait(self.timeout)

    @property
    def message(self) -> str:
        """Returns the current message (thread-safe)."""
        with self._message_lock:
            return self._message

    @message.setter
    def message(self, value: str) -> None:
        """Sets a new message (thread-safe)."""
        with self._message_lock:
            self._message = value
            logger.debug(f"VaMuPu message updated: {value[:100]}...")

    def update_message(self, message: str) -> None:
        """Updates the message to send.

        Args:
            message: New message
        """
        self.message = message


class VaultMultiListener(StoppableWorker):
    """Multicast Listener for Vault messages."""

    recv_signal = PySignal.ClassSignal()

    def __init__(
            self,
            group: str = DEFAULT_MULTICAST_GROUP,
            port: int = DEFAULT_PORT,
            timeout: float = DEFAULT_TIMEOUT,
            buffer_size: int = DEFAULT_BUFFER_SIZE,
            callback: Optional[Callable[[dict], None]] = None
    ):
        """Initializes the Multicast Listener.

        Args:
            group: Multicast group address
            port: UDP port
            timeout: Socket timeout in seconds
            buffer_size: Size of receive buffer
            callback: Optional callback function for received messages
        """
        super().__init__()
        self.group = group
        self.port = port
        self.timeout = timeout
        self.buffer_size = buffer_size
        self.callback = callback

        # Initialize socket
        self._sock: Optional[socket.socket] = None
        self._init_socket()

    def _init_socket(self) -> None:
        """Initializes the multicast socket."""
        try:
            logger.info(f"Starting multicast listener on {self.group}:{self.port}")

            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(('', self.port))

            # Join multicast group
            multicast_register = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, multicast_register)

            self._sock.settimeout(self.timeout)
            logger.debug("VaMuLi socket initialized.")

        except Exception as e:
            logger.error(f"Failed to initialize listener socket: {e}")
            raise

    def _cleanup_resources(self) -> None:
        """Closes the socket."""
        if self._sock:
            try:
                self._sock.close()
                logger.debug("VaMuLi socket closed.")
            except Exception as e:
                logger.error(f"Error closing listener socket: {e}")
            finally:
                self._sock = None

    def run(self) -> None:
        """Main loop for receiving multicast messages."""
        logger.info("VaMuLi entering receive loop")

        while not self._stop_event.is_set():
            try:
                # Receive message
                data, address = self._sock.recvfrom(self.buffer_size)

                # Parse JSON
                try:
                    json_data = json.loads(data.decode("utf-8"))
                    logger.debug(f"mc recv {json_data} from {address}")
                    logger.info(f"mc recv {json_data}")

                    # Emit signal
                    self.recv_signal.emit(json_data)

                    # Optional: Call callback
                    if self.callback:
                        try:
                            self.callback(json_data)
                        except Exception as cb_error:
                            logger.error(f"Callback error: {cb_error}")

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from {address}: {e}")
                except UnicodeDecodeError as e:
                    logger.warning(f"Invalid UTF-8 data from {address}: {e}")

            except socket.timeout:
                # Normal during wait time - not an error
                continue
            except OSError as e:
                # Socket was closed (by stop())
                if self._stop_event.is_set():
                    break
                logger.error(f"Socket error: {e}")
                break
            except Exception as e:
                logger.error(f"Unexpected error in receive loop: {e}", exc_info=True)
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)  # Short delay on unexpected errors


def main():
    """Example application."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Test listener
    logger.info("=== Testing Listener ===")
    with VaultMultiListener() as listener:
        try:
            input("Press Enter to stop listener...\n")
        except KeyboardInterrupt:
            pass

    # Test publisher
    logger.info("\n=== Testing Publisher ===")
    msg = {
        "type": "vault-test",
        "ip": ["127.0.0.1"],
        "port": "2004",
        "timestamp": time.time()
    }

    with VaultMultiPublisher(message=json.dumps(msg), timeout=1) as publisher:
        try:
            input("Press Enter to stop publisher...\n")
        except KeyboardInterrupt:
            pass

    logger.info("Done.")


if __name__ == "__main__":
    main()
    