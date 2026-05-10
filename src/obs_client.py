import obsws_python as obs


class OBSClient:
    def __init__(self):
        self.client = None
        self.connected = False

    def connect(self, host="localhost", port=4455, password=""):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client = obs.ReqClient(host=host, port=port, password=password, timeout=5)
        self.connected = True

    def disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.connected = False

    def start_recording(self):
        if not self.connected or self.client is None:
            raise RuntimeError("OBSに接続されていません")
        self.client.start_record()

    def stop_recording(self):
        if not self.connected or self.client is None:
            raise RuntimeError("OBSに接続されていません")
        self.client.stop_record()
