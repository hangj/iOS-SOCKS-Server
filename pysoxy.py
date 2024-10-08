#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
 Small Socks5 Proxy Server in Python
 from https://github.com/MisterDaneel/
"""

"""
 https://www.rfc-editor.org/rfc/rfc1928.html
"""

URL_GFW_LIST = "https://cdn.jsdelivr.net/gh/gfwlist/gfwlist/gfwlist.txt"

# Network
import socket
import select
from struct import pack, unpack
# System
import traceback
from threading import Thread, activeCount
from signal import signal, SIGINT, SIGTERM
from time import sleep
import sys

#
# Configuration
#
MAX_THREADS = 200
BUFSIZE = 2048
TIMEOUT_SOCKET = 5
LOCAL_ADDR = '0.0.0.0'
LOCAL_PORT = 9050
# Parameter to bind a socket to a device, using SO_BINDTODEVICE
# Only root can set this option
# If the name is an empty string or None, the interface is chosen when
# a routing decision is made
# OUTGOING_INTERFACE = "eth0"
OUTGOING_INTERFACE = ""

#
# Constants
#
'''Version of the protocol'''
# PROTOCOL VERSION 5
VER = b'\x05'
'''Method constants'''
# '00' NO AUTHENTICATION REQUIRED
M_NOAUTH = b'\x00'
# 'FF' NO ACCEPTABLE METHODS
M_NOTAVAILABLE = b'\xff'
'''Command constants'''
# CONNECT '01'
CMD_CONNECT = b'\x01'
CMD_BIND = b'\x02'
CMD_UDP_ASSOCIATE = b'\x03'
'''Address type constants'''
# IP V4 address '01'
ATYP_IPV4 = b'\x01'
# DOMAINNAME '03'
ATYP_DOMAINNAME = b'\x03'
ATYP_IPV6 = b'\x04'


class ExitStatus:
    """ Manage exit status """
    def __init__(self):
        self.exit = False

    def set_status(self, status):
        """ set exist status """
        self.exit = status

    def get_status(self):
        """ get exit status """
        return self.exit


def error(msg="", err=None):
    """ Print exception stack trace python """
    if msg:
        traceback.print_exc()
        print(f"{msg} - err: {err}", file=sys.stderr)
    else:
        traceback.print_exc()


def proxy_loop(socket_src, socket_dst):
    """ Wait for network activity """
    while not EXIT.get_status():
        try:
            reader, _, _ = select.select([socket_src, socket_dst], [], [], 1)
        except select.error as err:
            error("Select failed", err)
            return
        if not reader:
            continue
        try:
            for sock in reader:
                data = sock.recv(BUFSIZE)
                if not data:
                    return
                if sock is socket_dst:
                    socket_src.send(data)
                else:
                    socket_dst.send(data)
        except socket.error as err:
            error("Loop failed", err)
            return


def connect_to_dst(dst_addr, dst_port):
    """ Connect to desired destination """
    sock = create_socket()
    if OUTGOING_INTERFACE:
        try:
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_BINDTODEVICE,
                OUTGOING_INTERFACE.encode(),
            )
        except PermissionError as err:
            print(f"Only root can set OUTGOING_INTERFACE parameter: {err}", file=sys.stderr)
            EXIT.set_status(True)
    try:
        sock.connect((dst_addr, dst_port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock
    except socket.error as err:
        error("Failed to connect to DST", err)
        return 0


def request_client(wrapper):
    """ Client request details """
    # +----+-----+-------+------+----------+----------+
    # |VER | CMD |  RSV  | ATYP | DST.ADDR | DST.PORT |
    # +----+-----+-------+------+----------+----------+
    # | 1  |  1  | X'00' |  1   | Variable |    2     |
    # +----+-----+-------+------+----------+----------+
    try:
        s5_request = wrapper.recv(BUFSIZE)
    except ConnectionResetError:
        if wrapper != 0:
            wrapper.close()
        error()
        return False
    # Check VER, CMD and RSV
    if (
            s5_request[0:1] != VER or
            s5_request[1:2] != CMD_CONNECT or
            s5_request[2:3] != b'\x00'
    ):
        return False
    # IPV4
    if s5_request[3:4] == ATYP_IPV4:
        dst_addr = socket.inet_ntoa(s5_request[4:8])
        dst_port = unpack('>H', s5_request[8:10])[0]
    # DOMAIN NAME
    elif s5_request[3:4] == ATYP_DOMAINNAME:
        sz_domain_name = s5_request[4]
        # dst_addr = s5_request[5: 5 + sz_domain_name - len(s5_request)]
        dst_addr = s5_request[5: 5 + sz_domain_name]
        # port_to_unpack = s5_request[5 + sz_domain_name:len(s5_request)]
        port_to_unpack = s5_request[5 + sz_domain_name: 5 + sz_domain_name + 2]
        dst_port = unpack('>H', port_to_unpack)[0]
    else:
        return False
    print(dst_addr, dst_port)
    return (dst_addr, dst_port)


def request(wrapper):
    """
        The SOCKS request information is sent by the client as soon as it has
        established a connection to the SOCKS server, and completed the
        authentication negotiations.  The server evaluates the request, and
        returns a reply
    """
    dst = request_client(wrapper)
    socket_dst = 0
    # Server Reply
    # +----+-----+-------+------+----------+----------+
    # |VER | REP |  RSV  | ATYP | BND.ADDR | BND.PORT |
    # +----+-----+-------+------+----------+----------+
    # | 1  |  1  | X'00' |  1   | Variable |    2     |
    # +----+-----+-------+------+----------+----------+
    # o  VER    protocol version: X'05'
    # o  REP    Reply field:
    #     o  X'00' succeeded
    #     o  X'01' general SOCKS server failure
    #     o  X'02' connection not allowed by ruleset
    #     o  X'03' Network unreachable
    #     o  X'04' Host unreachable
    #     o  X'05' Connection refused
    #     o  X'06' TTL expired
    #     o  X'07' Command not supported
    #     o  X'08' Address type not supported
    #     o  X'09' to X'FF' unassigned
    # o  RSV    RESERVED
    # o  ATYP   address type of following address
    #     o  IP V4 address: X'01'
    #        o  DOMAINNAME: X'03'
    #        o  IP V6 address: X'04'
    # o  BND.ADDR       server bound address
    # o  BND.PORT       server bound port in network octet order

    rep = b'\x07'
    bnd = b'\x00' + b'\x00' + b'\x00' + b'\x00' + b'\x00' + b'\x00'
    if dst:
        socket_dst = connect_to_dst(dst[0], dst[1])
    if not dst or socket_dst == 0:
        rep = b'\x01'
    else:
        rep = b'\x00'
        bnd = socket.inet_aton(socket_dst.getsockname()[0])
        bnd += pack(">H", socket_dst.getsockname()[1])
    reply = VER + rep + b'\x00' + ATYP_IPV4 + bnd
    try:
        wrapper.sendall(reply)
    except socket.error:
        if wrapper != 0:
            wrapper.close()
        return
    # start proxy
    if rep == b'\x00':
        proxy_loop(wrapper, socket_dst)
    sleep(1)
    if wrapper != 0:
        wrapper.close()
    if socket_dst != 0:
        socket_dst.close()


def subnegotiation_client(wrapper):
    """
        The client connects to the server, and sends a version
        identifier/method selection message
    """
    # Client Version identifier/method selection message
    # +----+----------+----------+
    # |VER | NMETHODS | METHODS  |
    # +----+----------+----------+
    # | 1  |    1     | 1 to 255 |
    # +----+----------+----------+
    try:
        identification_packet = wrapper.recv(BUFSIZE)
    except socket.error:
        error()
        return M_NOTAVAILABLE
    # VER field
    if VER != identification_packet[0:1]:
        return M_NOTAVAILABLE
    # METHODS fields
    nmethods = identification_packet[1]
    methods = identification_packet[2:]
    if len(methods) != nmethods:
        return M_NOTAVAILABLE
    for method in methods:
        if method == ord(M_NOAUTH):
            return M_NOAUTH
    return M_NOTAVAILABLE


def subnegotiation(wrapper):
    """
        The client connects to the server, and sends a version
        identifier/method selection message
        The server selects from one of the methods given in METHODS, and
        sends a METHOD selection message
    """
    method = subnegotiation_client(wrapper)
    # Server Method selection message
    # +----+--------+
    # |VER | METHOD |
    # +----+--------+
    # | 1  |   1    |
    # +----+--------+
    if method != M_NOAUTH:
        return False
    reply = VER + method
    try:
        wrapper.sendall(reply)
    except socket.error:
        error()
        return False
    return True


def connection(wrapper):
    """ Function run by a thread """
    if subnegotiation(wrapper):
        request(wrapper)


def create_socket():
    """ Create an INET, STREAMing socket """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SOCKET)
    except socket.error as err:
        error("Failed to create socket", err)
        sys.exit(0)
    return sock


def bind_port(sock):
    """
        Bind the socket to address and
        listen for connections made to the socket
    """
    try:
        print('Bind {}'.format(str(LOCAL_PORT)))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.bind((LOCAL_ADDR, LOCAL_PORT))
    except socket.error as err:
        error("Bind failed", err)
        sock.close()
        sys.exit(0)
    # Listen
    try:
        sock.listen(10)
    except socket.error as err:
        error("Listen failed", err)
        sock.close()
        sys.exit(0)
    return sock


def exit_handler(signum, frame):
    """ Signal handler called with signal, exit script """
    print('Signal handler called with signal', signum)
    EXIT.set_status(True)


def create_wpad_server(hhost, hport, phost, pport):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import base64


    rules = None

    try:
        import http.client
        import re

        m = re.match('https://(.*?)/', URL_GFW_LIST)
        host = m.groups()[0]
        conn = http.client.HTTPSConnection(host)
        conn.request("GET", URL_GFW_LIST)
        res = conn.getresponse()
        data = res.read()

        text = base64.b64decode(data).decode()
        arr = text.split('\n')
        arr.append(f"@@||{phost}")
        it = filter(lambda x: x, arr) # filter empty lines
        next(it) # ignore the first line
        text = '",\n"'.join(it)
        rules = f'var rules = [\n"{text}"\n];\n'
    except Exception as e:
        print('get gfwlist error:', e, file=sys.stderr)
        pass

    class HTTPHandler(BaseHTTPRequestHandler):
        def do_HEAD(s):
            s.send_response(200)
            s.send_header("Content-type", "application/x-ns-proxy-autoconfig")
            s.end_headers()

        def do_GET(s):
            pac = None
            try:
                with open('proxy.pac') as f:
                    import re
                    pac = f.read()
                    # pac = re.sub('SOCKS5.*?DIRECT', f'SOCKS5 {phost}:{pport}; SOCKS {phost}:{pport}; DIRECT', pac)
            except Exception as e:
                print('open proxy.pac error:', e, file=sys.stderr)
                pass
            s.send_response(200)
            s.send_header("Content-type", "application/x-ns-proxy-autoconfig")
            s.end_headers()

            s.wfile.write(f'var proxy = "SOCKS5 {phost}:{pport}; SOCKS {phost}:{pport}; DIRECT;";\n'.encode())
            if rules:
                s.wfile.write(rules.encode())
            if pac:
                s.wfile.write(pac.encode())
            else:
                s.wfile.write("""
function FindProxyForURL(url, host)
{
   if (isInNet(host, "192.168.0.0", "255.255.0.0")) {
      return "DIRECT";
   }
   if (isInNet(host, "172.16.0.0", "255.240.0.0")) {
      return "DIRECT";
   }
   if (isInNet(host, "10.0.0.0", "255.0.0.0")) {
      return "DIRECT";
   }
   return proxy;
}
""".encode())

    HTTPServer.allow_reuse_address = True
    server = HTTPServer((hhost, hport), HTTPHandler)
    return server


def run_wpad_server(server):
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass



def main():
    PROXY_HOST = "172.20.10.1"
    SOCKS_HOST = "0.0.0.0"
    WPAD_PORT = 8080
    wpad_server = create_wpad_server(
        SOCKS_HOST, WPAD_PORT, PROXY_HOST, LOCAL_PORT
    )

    print("PAC URL: http://{}:{}/proxy.pac".format(PROXY_HOST, WPAD_PORT))
    print("SOCKS Address: {}:{}".format(PROXY_HOST or SOCKS_HOST, LOCAL_PORT))

    thread = Thread(target=run_wpad_server, args=(wpad_server,))
    thread.daemon = True
    thread.start()


    """ Main function """
    new_socket = create_socket()
    bind_port(new_socket)
    signal(SIGINT, exit_handler)
    signal(SIGTERM, exit_handler)
    while not EXIT.get_status():
        if activeCount() > MAX_THREADS:
            sleep(3)
            continue
        try:
            wrapper, _ = new_socket.accept()
            wrapper.setblocking(1)
        except socket.timeout:
            continue
        except socket.error as err:
            error("Accept socket error", err)
            continue
        except TypeError as err:
            error("Accept TypeError", err)
            sys.exit(0)
        recv_thread = Thread(target=connection, args=(wrapper, ))
        recv_thread.start()
    new_socket.close()


EXIT = ExitStatus()
if __name__ == '__main__':
    main()
