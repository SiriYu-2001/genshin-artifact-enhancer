"""Opt-in release acceptance guard: fail external Python network access."""
import ipaddress
import sys


def install():
    def audit(event,args):
        if event=='socket.getaddrinfo':host=args[0]
        elif event=='socket.connect':host=args[1][0] if isinstance(args[1],tuple) else ''
        else:return
        if host in ('localhost',None):return
        try:allowed=ipaddress.ip_address(host).is_loopback
        except ValueError:allowed=False
        if not allowed:raise RuntimeError('离线验收禁止访问外网：'+str(host))
    sys.addaudithook(audit)
