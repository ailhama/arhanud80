from server import Handler
from cloud_store import CloudStore


class handler(Handler):
    def get_store(self):
        return CloudStore()

    def do_GET(self): self.send_error(405)
    def do_HEAD(self): self.send_error(405)
