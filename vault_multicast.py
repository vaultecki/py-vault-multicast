import logging
import json
import socket
import struct
import threading
import time
import PySignal

logger = logging.getLogger(__name__)


class StoppableWorker(object):
    """Basisklasse für Threads, die sauber gestartet und gestoppt werden können."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Erstellt und startet den Thread."""
        if self._thread is None or not self._thread.is_alive():
            logger.debug(f"{self.__class__.__name__} starting thread.")
            # Die Methode self.run() muss von den Unterklassen implementiert werden.
            self._thread = threading.Thread(target=self.run)
            self._thread.daemon = True
            self._thread.start()

    def stop(self):
        """Setzt das Event und wartet auf das Beenden des Threads."""
        logger.debug(f"{self.__class__.__name__} stopping.")
        self._stop_event.set()

        # Unterklassen sollten spezifische Ressourcen (z.B. Socket) schließen
        # bevor join() aufgerufen wird, um Blockaden zu beenden.

        if self._thread:
            self._thread.join()

    def run(self):
        """Die Haupt-Arbeitsschleife des Threads. MUSS in der Unterklasse überschrieben werden."""
        raise NotImplementedError("Die Methode 'run()' muss in der Unterklasse implementiert werden.")


class VaultMultiPublisher(StoppableWorker):
    def __init__(self, group="224.1.1.1", port=5004, ttl=2, timeout=2, message="there is nothing to see"):
        super().__init__()
        self.group = group
        self.port = port
        self.ttl = ttl
        self._stop_event = threading.Event()  # Neues Stopp-Event

        self.timeout = timeout
        self.message = message
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        self._thread = None

    def run(self):
        """Haupt-Schleife für das Senden von Multicast-Nachrichten."""
        logger.debug("VaMuPu start main advertisment loop")
        if not self._stop_event.wait(5):
            while not self._stop_event.is_set():
                logger.debug("VaMuPu pub: {}".format(self.message.encode("utf-8")))

                try:
                    self._sock.sendto(self.message.encode("utf-8"), (self.group, self.port))
                except Exception as e:
                    logger.error(f"Error sending multicast: {e}")
                    # Hier könnte man entscheiden, ob der Thread gestoppt werden soll

                # Wartet für 'timeout' Sekunden ODER bis das Event gesetzt wird
                self._stop_event.wait(self.timeout)

    def update_message(self, message):
        logger.debug("VaMuPu msg update: {}".format(message))
        self.message = message


class VaultMultiListener(StoppableWorker):
    recv_signal = PySignal.ClassSignal()

    def __init__(self, group="224.1.1.1", port=5004, timeout=2):
        super().__init__()
        self.timeout = timeout
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('', port))
        multicast_register = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, multicast_register)

        self._stop_event = threading.Event()
        self._sock.settimeout(timeout)
        self._thread = None

    def run(self):
        """Haupt-Schleife für den Multicast-Empfang."""
        while not self._stop_event.is_set():
            try:
                # sock.recvfrom blockiert maximal 'timeout' Sekunden
                message, address = self._sock.recvfrom(1400)
                json_data = json.loads(message.decode("utf-8"))

                logger.debug(f"mc recv {json_data} from {address}")
                self.recv_signal.emit(json_data)

            except socket.timeout:
                # Dies ist im Normalbetrieb erwartet (keine Nachricht empfangen)
                pass
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON: {e}")
            except Exception as e:
                # Andere unerwartete Fehler (z.B. socket closed)
                logger.error(f"Unexpected socket error: {e}")
                if self._stop_event.is_set():
                    break  # Wahrscheinlich durch stop() ausgelöst
                time.sleep(0.1)  # Kurzer Delay bei unerwarteten Fehlern


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    listener = VaultMultiListener()
    try:
        input("Press enter to exit...\n\n")
    finally:
        listener.stop()

    msg = {"type": "vault-test", "ip": ["127.0.0.1"], "port": "2004"}
    publisher = VaultMultiPublisher(message=json.dumps(msg, indent=0))
    try:
        input("Press enter to exit...\n\n")
    finally:
        publisher.stop()
