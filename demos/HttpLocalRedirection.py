#!//usr/bin/env python
# -*- coding: utf-8 -*-

import sys
sys.path.append("../src/")
from TcpLocalRedirection import TcpLocalRedirection, Forward
import mimetypes
from wsgiref.handlers import format_date_time
from datetime import datetime
from time import mktime
import logging


class HttpForward(Forward):
    MAPPING = {
        "/HttpRedirection.py": "./HttpRedirection.py",
    }

    def __init__(self, proxy, client, client_addr):
        Forward.__init__(self, proxy, client, client_addr)

    def http_response(self, path):
        now = datetime.now()
        stamp = mktime(now.timetuple())
        dt = format_date_time(stamp)

        tp = mimetypes.guess_type(path)[0]
        if tp is None:
            tp = "application/octet-stream"

        with open(self.MAPPING[path], "rb") as fp:
            body = fp.read()

        http_response_tpl = [
            "HTTP/1.1 200 OK",
            "Date: %s" % dt,
            "Server: HttpRedirectionServer",
            "Content-Length: %s" % len(body),
            "Content-Type: %s" % tp,
            "Connection: keep-alive",
            "",
            body
        ]

        return "\r\n".join(http_response_tpl)

    def process_down_recv(self, data):
        """处理从Client接收到的数据
        @param data: 从Client接收到的数据
        @return: (processed_recv_data, response_data)
                processed_recv_data: 对接收到的数据进行处理, 处理完之后再转发到远程
                response_data: 返回给Client的数据
        """
        for path in self.MAPPING:
            if data.startswith("GET %s" % path):
                return ("", self.http_response(path))
        else:
            data = data.replace("%s:%d" % self.proxy.bind_addr, "%s:%d" % self.proxy.remote_addr)

        return (data, None)

def main():
    import os
    logging.basicConfig(level=logging.DEBUG)
    work_dir = os.path.dirname(__file__)
    if work_dir:
        os.chdir(work_dir)
    bind_addr = ("127.0.0.1", 1213)
    remote_addr = ("127.0.0.1", 80)
    lrd = TcpLocalRedirection(bind_addr, remote_addr, forward=HttpForward)
    lrd.main_loop()

if __name__ == '__main__':
    main()
