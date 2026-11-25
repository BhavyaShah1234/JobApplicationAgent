"""Authenticated X11 transport over loopback to this container's sole X server."""
import select
import socket
import socketserver


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as upstream:
            upstream.connect('/tmp/.X11-unix/X102')
            sockets=[self.request,upstream]
            while True:
                ready,_,_=select.select(sockets,[],[],30)
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    (upstream if source is self.request else self.request).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


Server(('127.0.0.1',6102),Handler).serve_forever()
