#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 
# [up] remote server <- local redir <- client [down]
# 

import select
import socket
import errno
import time
import logging
import mimetypes


class Connection(object):
    def __init__(self, sock, addr, max_buf_size):
        self.sock = sock
        self.addr = addr
        self.create = time.time()
        self.rbuf = ""
        self.max_buf_size = max_buf_size

    @property
    def rbuf_full(self):
        return len(self.rbuf) >= self.max_buf_size

    def recv(self):
        """
        @return received size. -1表示连接断开
        """
        rsize = 0
        try:
            while True:
                buf_left = self.max_buf_size - len(self.rbuf)
                data = self.sock.recv(buf_left)
                if data:
                    self.rbuf += data
                    rsize += len(data)
                    if self.rbuf_full:
                        return rsize
                else:
                    return -1   # connection closed.
        except socket.error, e:
            if e.args[0] not in (errno.EWOULDBLOCK, errno.EAGAIN):
                return -1
        return rsize

    def send(self, data):
        """
        @return sended size, -1表示连接断开
        """
        ssize = 0
        try:
            # while data:
            #     size = self.sock.send(data)
            #     data = data[size:]
            #     ssize += size
            ssize = self.sock.send(data)
        except socket.error, e:
            if e.args[0] not in (errno.EWOULDBLOCK, errno.EAGAIN):
                return -1   # socket closed
        return ssize


class Forward(object):
    def __init__(self, proxy, client, client_addr):
        remote_addr = proxy.remote_addr
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote.connect(proxy.remote_addr)   # remote-sock is blocking now.
        except socket.error, e:
            logging.warning("Connect to remote[%s:%s] failed." % remote_addr)
            remote.close()
            client.close()
            return
        remote.setblocking(0)

        self.proxy = proxy
        self.down = Connection(client, client_addr, self.proxy.max_buf_size)
        self.up = Connection(remote, remote_addr, self.proxy.max_buf_size)
        proxy._forwards[client] = self
        proxy._forwards[remote] = self
        proxy.r_list.add(client)
        proxy.r_list.add(remote)

    def close(self):
        for conn in (self.up, self.down):
            sock = conn.sock
            self.proxy._forwards.pop(sock, None)
            self.proxy.r_list.discard(sock)
            self.proxy.w_list.discard(sock)
            sock.close()

    def on_recv(self, sock):
        if self.up.sock is sock:
            conn, other, name = self.up, self.down, "Up"
        elif self.down.sock is sock:
            conn, other, name = self.down, self.up, "Down"
        else:   # never happen
            assert 0, "wimp out?"

        rsize = conn.recv()
        if rsize == -1:
            self.close()
        else:
            logging.debug("Recv from %s: %d", name, rsize)
            process = self.process_up_recv if conn is self.up else self.process_down_recv
            rdata, sdata = process(conn.rbuf)
            if rdata is not None:
                conn.rbuf = rdata
            if sdata is not None:
                other.rbuf += sdata
                if other.rbuf:
                    self.proxy.w_list.add(conn.sock)
            if conn.rbuf:
                self.proxy.w_list.add(other.sock)
            if conn.rbuf_full:
                self.proxy.r_list.discard(conn.sock)

    def on_send(self, sock):
        if self.up.sock is sock:
            conn, other, name = self.up, self.down, "Up"
        elif self.down.sock is sock:
            conn, other, name = self.down, self.up, "Down"
        else:   # never happen
            assert 0, "wimp out?"
        if not other.rbuf:
            return
        ssize = conn.send(other.rbuf)
        if ssize == -1:
            self.close()
        else:
            logging.debug("Send to %s: %d", name, ssize)
            other.rbuf = other.rbuf[ssize:]
            if not other.rbuf:
                self.proxy.w_list.discard(conn.sock)
            if not other.rbuf_full:
                self.proxy.r_list.add(other.sock)

    # ----- 对转发进行拦截 -----

    def process_down_recv(self, data):
        """处理从Client接收到的数据
        @param data: 从Client接收到的数据
        @return: (processed_recv_data, response_data)
                processed_recv_data: 对接收到的数据进行处理, 处理完之后再转发到远程 
                response_data: 返回给Client的数据
                (None表示不处理)
        """
        return (None, None)

    def process_up_recv(self, data):
        """处理从Remote接收到的数据
        @param data: 从Remote接收到的数据
        @return: (processed_recv_data, response_data)
                processed_recv_data: 对接收到的数据进行处理, 处理完之后再转发到Client
                response_data: 返回给Remote的数据
                (None表示不处理)
        """
        return (None, None)


class TcpLocalRedirection(object):
    def __init__(self, bind_addr, remote_addr, max_buf_size=1024*64, forward=None):
        self.bind_addr = bind_addr
        self.remote_addr = remote_addr
        self.max_buf_size = max_buf_size

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR  , 1)
        sock.bind(bind_addr)
        sock.listen(200)
        self.server = sock
        self.Forward = forward if forward else Forward

        self._forwards = {}
        self.w_list = set()
        self.r_list = set()
        self.r_list.add(self.server)

    def main_loop(self):
        def call(sock, method):
            forward = self._forwards.get(sock)
            if forward:
                getattr(forward, method)(sock)
            else:
                logging.error("Close [unkown connection].")
                self.r_list.discard(sock)
                self.w_list.discard(sock)
                sock.close()

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
                            logging.info("Accept conn[%s:%s]" % client_addr)
                            self.Forward(self, client, client_addr)
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
    bind_addr = ("127.0.0.1", 1234)
    remote_addr = ("127.0.0.1", 22)
    lrd = TcpLocalRedirection(bind_addr, remote_addr)
    lrd.main_loop()








