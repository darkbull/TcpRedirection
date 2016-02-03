#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 
# [up] remote server <- local redir <- client [down]
# 

import select
import socket
import errno
import logging
import mimetypes


class Forward(object):
    def __init__(self, proxy, client, client_addr):
        self.proxy = proxy
        self.client = client
        self.client_addr = client_addr
        remote_addr = proxy.remote_addr
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote.connect(proxy.remote_addr)   # remote-sock is blocking now.
        except socket.error, e:
            logging.warning("Connect to remote[%s:%s] failed." % remote_addr)
            remote.close()
            client.close()
            return

        # connect to remote-host success
        remote.setblocking(0)
        self.remote = remote
        self.remote_send_buf = ""
        self.client_send_buf = ""
        proxy._forwards[client] = self
        proxy._forwards[remote] = self
        proxy.r_list.add(self.client)
        proxy.r_list.add(self.remote)

    def close(self):
        for sock in (self.client, self.remote):
            self.proxy._forwards.pop(sock, None)
            self.proxy.r_list.discard(sock)
            self.proxy.w_list.discard(sock)
            sock.close()

    def on_recv(self, sock):
        rdata = []
        try:
            while True:
                data = sock.recv(1024)
                if not data:
                    self.close()
                    return
                rdata.append(data)
        except socket.error, e:
            if e.args[0] not in (errno.EWOULDBLOCK, errno.EAGAIN):
                self.close()
                return

        data = "".join(rdata)
        if not data:
            return
        if sock is self.client:
            logging.debug("Recv from client:\n%s", data)
            self.remote_send_buf += data
            if len(self.remote_send_buf) > self.proxy.max_buf_size:
                logging.warning("Remote send buffer out of size")
                self.close()
                return
            self.proxy.w_list.add(self.remote)
        elif sock is self.remote:
            self.client_send_buf += data
            if len(self.client_send_buf) > self.proxy.max_buf_size:
                logging.warning("Client send buffer out of size")
                self.close()
                return
            self.proxy.w_list.add(self.client)

    def on_send(self, sock):
        try:
            if sock is self.client:
                if self.client_send_buf:
                    size = sock.send(self.client_send_buf)
                    self.client_send_buf = self.client_send_buf[size:]
                    if not self.client_send_buf:
                        self.proxy.w_list.discard(sock)
                else:
                    self.proxy.w_list.discard(sock)
            elif sock is self.remote:
                if self.remote_send_buf:
                    size = sock.send(self.remote_send_buf)
                    self.remote_send_buf = self.remote_send_buf[size:]
                    if not self.remote_send_buf:
                        self.proxy.w_list.discard(sock)
                else:
                    self.proxy.w_list.discard(sock)
        except socket.error, e:
            if e.args[0] not in (errno.EWOULDBLOCK, errno.EAGAIN):
                self.close()


class LocalRedirection(object):
    def __init__(self, bind_addr, remote_addr, max_buf_size=1024*1024):
        self.bind_addr = bind_addr
        self.remote_addr = remote_addr
        self.max_buf_size = max_buf_size

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR  , 1)
        sock.bind(bind_addr)
        sock.listen(200)
        self.server = sock

        self._forwards = {}
        self.w_list = set()
        self.r_list = set()
        self.r_list.add(self.server)

    def main_loop(self):
        def call(sock, method):
            forward = self._forwards.get(sock)
            if forward:
                getattr(forward, method)(sock)

        timeout = 0.1
        while True:
            r_list, w_list, e_list = select.select(self.r_list, self.w_list, self.r_list, timeout)
            for sock in e_list:
                call(sock, "close")
            for sock in r_list:
                if sock is self.server:
                    while True:
                        try:
                            client, client_addr = sock.accept()
                            client.setblocking(0)
                            logging.debug("Accept conn[%s:%s]" % client_addr)
                            Forward(self, client, client_addr)
                        except socket.error, e:
                            if e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN):
                                break
                            raise
                else:
                    call(sock, "on_recv")
            for sock in w_list:
                call(sock, "on_send")


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    lrd = LocalRedirection(("127.0.0.1", 1213), ("your-host", 80))
    lrd.main_loop()








