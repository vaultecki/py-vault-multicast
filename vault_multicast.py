import json
import socket
import struct
import threading
import time
import PySignal


class VaultMultiPublisher:
    def __init__(self, group="224.1.1.1", port=5004, ttl=2, timeout=2, message="there is nothing to see", debug=False):
        self.group = group
        self.port = port
        self.ttl = ttl
        self.stop_advertising = False
        self.debug = debug
        self.timeout = timeout
        self.message = message
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        threading.Timer(5, self.advertise).start()

    def update_message(self, message):
        if self.debug:
            print("bsd msg update: {}".format(message))
        self.message = message

    def advertise(self):
        if self.debug:
            print("bsd pub: {}".format(self.message.encode("utf-8")))
        if not self.stop_advertising:
            self.sock.sendto(self.message.encode("utf-8"), (self.group, self.port))
        if not self.stop_advertising:
            threading.Timer(self.timeout, self.advertise).start()

    def stop(self):
        self.stop_advertising = True
        # time.sleep(self.timeout)
        self.sock.close()


class VaultMultiListener:
    recv_signal = PySignal.ClassSignal()

    def __init__(self, group="224.1.1.1", port=5004, timeout=2, debug=False):
        self.timeout = timeout
        self.debug = debug
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', port))
        multicast_register = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, multicast_register)
        self.stop_recv = False
        self.sock.settimeout(self.timeout)

    def start(self):
        threading.Timer(self.timeout, self.recv).start()

    def recv(self):
        while not self.stop_recv:
            json_data = {}
            try:
                message = self.sock.recvfrom(1400)
                json_data = json.loads(message[0].decode("utf-8"))
            except Exception as e:
                if self.debug:
                    print("error socket: {}".format(e))
                pass
            if json_data:
                if self.debug:
                    print("mc recv {}".format(json_data))
                self.recv_signal.emit(json_data)
            time.sleep(0.2)

    def stop(self):
        self.stop_recv = True
        # time.sleep(self.timeout + 1)
        self.sock.close()


if __name__ == "__main__":
    # listener = VaultMultiListener()
    # try:
    #     input("Press enter to exit...\n\n")
    # finally:
    #     listener.stop()
    msg = {"type": "vault-test", "ip": ["127.0.0.1"], "port": "2004"}
    publisher = VaultMultiPublisher(message=json.dumps(msg, indent=0))
    try:
        input("Press enter to exit...\n\n")
    finally:
        publisher.stop()
