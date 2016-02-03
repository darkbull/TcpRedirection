#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 
# [up]Server <-> RRDClient <-> RRDServer <-> Client[down]
# 
#   RRDClient启动时，会创建一定数的连接连接到RRDServer，并发送长度为10个字节(格式为"{[(xxxx)]}")到RRDServer，用于标识为内部链接
# 


import socket
import select
import errno
import time
import re
import random
import logging


class Connection(object):
    PRIVATE_HEAD_REGEX = re.compile(r"^\{\[\(\d{4}\)\]\}$")

    TP_PRIVATE = 1      # RRDServer与RRDClient之间的连接
    TP_OPEN = 2         # 外部链接
    TP_UNKNOWN = 3      # 未知

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.create = time.time()
        self.rbuf = ""

    @property
    def type(self):
        """当前连接的类型：
        """
        cls = type(self)
        if len(self.rbuf) >= 10:
            if cls.PRIVATE_HEAD_REGEX.match(self.rbuf[:10]):
                return cls.TP_PRIVATE
            else:
                return cls.TP_OPEN
        else:
            str1 = "{[(0000)]}"
            str2 = "{[(9999)]}"
            for idx, c in enumerate(self.rbuf):
                if 3 <= idx < 7:
                    if not (str1[idx] <= c <= str2[idx]):
                        return cls.TP_OPEN
                else:
                    if str1[idx] != c:
                        return cls.TP_OPEN
            return cls.TP_UNKNOWN

    def recv(self):
        """
        @return received size. -1表示连接断开
        """
        rsize = 0
        try:
            while True:
                data = self.sock.recv(1024)
                if data:
                    self.rbuf += data
                    if len(self.rbuf) > RemoteRedirection.max_buf_size:
                        return -1
                    rsize += len(data)
                else:
                    return -1
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
            while data:
                size = self.sock.send(data)
                data = data[size:]
                ssize += size
        except socket.error, e:
            if e.args[0] not in (errno.EWOULDBLOCK, errno.EAGAIN):
                return -1   # socket closed
        return ssize


class Forward(object):
    def __init__(self, proxy, up=None, down=None):
        self.proxy = proxy
        self.up = up
        self.down = down

    def close(self):
        for conn in (self.up, self.down):
            if conn:
                self.proxy._forwards.pop(conn.sock, None)
                self.proxy.r_list.discard(conn.sock)
                self.proxy.w_list.discard(conn.sock)
                conn.sock.close()
        if self in self.proxy.forward_pool:
            self.proxy.forward_pool.remove(self)

    def on_recv(self, sock):
        if self.up and sock is self.up.sock:
            rsize = self.up.recv()
            if rsize == -1:
                self.close()
            else:
                if self.up.rbuf and self.down:
                    self.proxy.w_list.add(self.down.sock)
        elif self.down and sock is self.down.sock:
            rsize = self.down.recv()
            if rsize == -1:
                self.close()
            else:
                if self.down.rbuf and self.up:
                    self.proxy.w_list.add(self.up.sock)

    def on_send(self, sock):
        if self.up and sock is self.up.sock:
            ssize = self.up.send(self.down.rbuf)
            if ssize == -1:
                self.close()
                return
            self.down.rbuf = self.down.rbuf[ssize:]
            if not self.down.rbuf:
                self.proxy.w_list.discard(sock)
        elif self.down and sock is self.down.sock:
            ssize = self.down.send(self.up.rbuf)
            if ssize == -1:
                self.close()
                return
            self.up.rbuf = self.up.rbuf[ssize:]
            if not self.up.rbuf:
                self.proxy.w_list.discard(sock)


class RemoteRedirection(object):
    max_buf_size = 1024*1024

    def __init__(self):
        self.forward_pool = []
        self._forwards = {}
        self.w_list = set()
        self.r_list = set()

    def get_forward_from_pool(self, sock):
        for fw in self.forward_pool:
            if (fw.up and fw.up.sock is sock) or (fw.down and fw.down.sock is sock):
                return fw

    def _call(self, sock, method):
        fw = self._forwards.get(sock)
        if not fw:
            fw = self.get_forward_from_pool(sock)
        if fw:
            getattr(fw, method)(sock)


class RRDServer(RemoteRedirection):
    def __init__(self, bind_addr):
        RemoteRedirection.__init__(self)
        self.conn_pool = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR  , 1)
        sock.bind(bind_addr)
        sock.listen(200)
        self.server = sock
        self.r_list.add(self.server)
        self.bind_addr = bind_addr

    def get_conn_from_pool(self, sock):
        for conn in self.conn_pool:
            if conn.sock is sock:
                return conn

    def main_loop(self):
        timeout = 0.1
        logging.info("RRDServer is running at %s:%s" % self.bind_addr)
        while True:
            r_list, w_list, e_list = select.select(self.r_list, self.w_list, self.r_list, timeout)
            for sock in e_list:
                conn = self.get_conn_from_pool(sock)
                if conn:
                    self.conn_pool.remove(conn)
                    self.w_list.discard(sock)
                    self.r_list.discard(sock)
                    sock.close()
                else:
                    self._call(sock, "close")
            for sock in r_list:
                if sock is self.server:
                    while True:
                        try:
                            client, client_addr = sock.accept()
                            client.setblocking(0)
                            conn = Connection(client, client_addr)
                            # we don't know it's a private-connection or open-connection, put it to conn_pool first.
                            self.conn_pool.append(conn) 
                            logging.debug("Accept conn [%s:%s]" % client_addr)
                            self.r_list.add(client)
                        except socket.error, e:
                            if e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN):
                                break
                            raise
                else:
                    conn = self.get_conn_from_pool(sock)
                    if conn:
                        rsize = conn.recv()
                        if rsize == -1:
                            self.r_list.discard(sock)
                            sock.close()
                            self.conn_pool.remove(conn)
                        else:
                            tp = conn.type
                            if tp == conn.TP_PRIVATE:
                                conn.rbuf = conn.rbuf[10:]  # strip head
                                fw = Forward(self, up=conn)
                                self.conn_pool.remove(conn)
                                self.forward_pool.append(fw)
                            elif tp == conn.TP_OPEN:
                                fw = Forward(self, down=conn)
                                self.conn_pool.remove(conn)
                                self.forward_pool.append(fw)
                            else:
                                pass    # we still don't know what's type of the connection, left it away.
                    else:
                        self._call(sock, "on_recv")
            for sock in w_list:
                self._call(sock, "on_send")

            if self.forward_pool:
                # group forward socket-pair
                ups = [fw for fw in self.forward_pool if fw.up]
                downs = [fw for fw in self.forward_pool if fw.down]
                for up, down in zip(ups, downs):
                    assert up.down is None
                    assert down.up is None
                    fw = up
                    fw.down = down.down
                    down.down = None
                    self.forward_pool.remove(up)
                    self.forward_pool.remove(down)
                    self._forwards[fw.up.sock] = fw
                    self._forwards[fw.down.sock] = fw
                    if fw.up.rbuf:
                        self.w_list.add(fw.down.sock)
                    if fw.down.rbuf:
                        self.w_list.add(fw.up.sock)


class RRDClient(RemoteRedirection):
    def __init__(self, rrd_server_addr, server_addr):
        RemoteRedirection.__init__(self)
        self.rrd_server_addr = rrd_server_addr
        self.server_addr = server_addr

    err_flag = False    # a flag, same error will only be loged once.
    def create_conn(self, addr):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(addr)
            sock.setblocking(0)
            self.r_list.add(sock)
            logging.debug("Create a conn to [%s:%s]" % addr)
            self.err_flag = False
            return Connection(sock, sock.getsockname())
        except socket.error, e:
            if e.args[0] not in (errno.EINPROGRESS, errno.EWOULDBLOCK):
                pass    # server busy
            if not self.err_flag:
                logging.warning("Connect to [%s:%s] fail: %s", addr[0], addr[1], e)
                self.err_flag = True
            if sock:
                sock.close()

    def full_poll(self):
        pool_size = 5
        poll_live_time = 60

        now = time.time()
        for fw in list(self.forward_pool):
            if now - fw.down.create > poll_live_time:
                fw.close()

        if len(self.forward_pool) < pool_size:
            for i in xrange(pool_size - len(self.forward_pool)):
                conn = self.create_conn(self.rrd_server_addr)
                if not conn:
                    break
                conn.sock.send("{[(%04d)]}" % random.randint(1, 9999))    # send private-connection flag
                fw = Forward(self, down=conn)
                self.forward_pool.append(fw)
                logging.info("Create [private-connection] to RRDServer.")


    def main_loop(self):
        timeout = 0.1
        while True:
            self.full_poll()
            r_list, w_list, e_list = select.select(self.r_list, self.w_list, self.r_list, timeout)
            for sock in e_list:
                self._call(sock, "close")
            for sock in r_list:
                fw = self.get_forward_from_pool(sock)
                if fw:
                    assert fw.up is None
                    rsize = fw.down.recv()
                    if rsize == -1:
                        fw.close()
                    else:
                        conn = self.create_conn(self.server_addr)
                        if conn:
                            fw.up = conn
                            self.forward_pool.remove(fw)
                            self._forwards[fw.up.sock] = fw
                            self._forwards[fw.down.sock] = fw
                            if fw.down.rbuf:
                                self.w_list.add(fw.up.sock)
                        else:
                            fw.close()
                            continue
                else:
                    self._call(sock, "on_recv")
            for sock in w_list:
                self._call(sock, "on_send")


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    import sys
    if sys.argv[1] == "server":
        bind = ("127.0.0.1", 1234)
        server = RRDServer(bind)
        server.main_loop()
    else:
        rrd_server = ("127.0.0.1", 1234)
        server = ("remote-host", 3000)
        client = RRDClient(rrd_server, server)
        client.main_loop()

