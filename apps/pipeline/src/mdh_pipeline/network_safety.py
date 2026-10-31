"""Network address checks shared by source readers; never used by contracts."""
import ipaddress
import socket

def require_public_dns(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Source resolved to a non-public network')

