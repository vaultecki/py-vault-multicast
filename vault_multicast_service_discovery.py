import json
import sys
import threading

import PyQt6.QtWidgets
import PyQt6.QtCore
import PySignal
import vault_multicast


class VaultServiceDiscovery(PyQt6.QtWidgets.QWidget):
    return_signal = PySignal.ClassSignal()

    def __init__(self, type_filter=""):
        super().__init__()
        main_layout = PyQt6.QtWidgets.QVBoxLayout()

        self.tree = PyQt6.QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Service", "IP / Port", "Data"])
        self.tree.setColumnHidden(2, True)
        self.ip_port_on_display = []
        self.filter = type_filter

        btn_connect = PyQt6.QtWidgets.QPushButton("Connect")
        btn_connect.clicked.connect(self.on_clicked_btn_connect)

        main_layout.addWidget(self.tree)
        main_layout.addWidget(btn_connect)
        self.setLayout(main_layout)

        # listener
        self.listener_timeout = 3
        self.listener_stop = False
        self.listener = vault_multicast.VaultMultiListener()
        self.listener.recv_signal.connect(self.on_update_connections)
        threading.Timer(1, self.listener_thread).start()

    def stop(self):
        self.listener_stop = True

    def listener_thread(self):
        if not self.listener_stop:
            try:
                self.listener.start()
            except Exception as e:
                print(e)
            threading.Timer(self.listener_timeout, self.listener_thread).start()
        else:
            self.listener.stop()

    def on_update_connections(self, server):
        if server.get("addr") not in self.ip_port_on_display and self.filter in server.get("type", False):
            new_tree_item = PyQt6.QtWidgets.QTreeWidgetItem(self.tree)
            new_tree_item.setText(0, server.get("name", "No name"))
            new_tree_item.setText(1, str(server.get("addr")))
            new_tree_item.setText(2, json.dumps(server, indent=0))
            self.ip_port_on_display.append(server.get("addr"))

    def on_clicked_btn_connect(self):
        tree_items = self.tree.selectedItems()
        if tree_items:
            tree_item_data = {}
            for tree_item in tree_items:
                tree_item_data = json.loads(tree_item.text(2))
            self.return_signal.emit(tree_item_data)
            self.stop()
            self.hide()


class TestMainWindow(PyQt6.QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.bsd = None
        self.bsd_data = False
        self.button = PyQt6.QtWidgets.QPushButton("Push for Window")
        self.button.clicked.connect(self.show_bsd_widget)
        self.setCentralWidget(self.button)

        # timer for showing networks
        timer = PyQt6.QtCore.QTimer()
        timer.timeout.connect(self.__check_bsd_widget)
        timer.start(500)

    def show_bsd_widget(self):
        print("test")
        self.bsd = VaultServiceDiscovery(type_filter="VaultLibrary")
        self.bsd.return_signal.connect(self.on_submit_value)
        self.bsd.show()

    def __check_bsd_widget(self):
        if self.bsd:
            if not self.bsd.isVisible():
                self.bsd.return_signal.disconnect(self.on_submit_value)
                self.bsd.stop()
                self.bsd = None

    def on_submit_value(self, value):
        print("Mainwindow: {}".format(value))
        self.bsd_data = value


class TestMainApp:
    def __init__(self):
        self.app = PyQt6.QtWidgets.QApplication(sys.argv)
        self.tmw = TestMainWindow()

    def run(self):
        self.tmw.show()
        self.app.exec()


if __name__ == "__main__":
    multi_window = TestMainApp()
    multi_window.run()
