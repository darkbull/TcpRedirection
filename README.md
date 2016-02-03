# TcpRedirection
本地Tcp转发、远程Tcp转发简单实现。功能与ssh的转发类似，但没有加密功能，仅仅只是简单的转发。

本地Tcp转发：
    [up] Remote-Server <-> Local-Redirection <-> Client [down]
    应用场景：Client无法直接访问Remote-Server，通过Local-Redirection将远程服务映射到某个地址供Client能够访问

远程Tcp转发
    [up]Server <-> RRDClient <-> RRDServer <-> Client[down]
    应用场景：Server可能在NAT内，公网用户无法直接访问到。在一台公网服务器上安装RRDServer，让NAT内可以访问到Server的某台机器运行RRDClient主动连接到RRDServer实现反向映射，其他公网用户通过访问RRDServer来间接访问Server.


