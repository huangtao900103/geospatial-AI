"""Explicit HTTP host allowlist; container binding does not disable origin checks."""
import ipaddress
import re
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset(('127.0.0.1','localhost'))


def allowed_hostname(value):
    if not isinstance(value,str) or not value or value!=value.strip() or '*' in value:
        raise ValueError('Allowed hosts must be exact hostnames or IP addresses, without ports or wildcards')
    try:return str(ipaddress.ip_address(value))
    except ValueError:
        value=value.lower()
        if len(value)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in value.split('.')):
            raise ValueError('Allowed hosts must be exact hostnames or IP addresses, without schemes, paths or ports')
        return value


def request_hostname(value):
    if not isinstance(value,str) or not value or re.search(r'[\s,/@?#\\]',value):return None
    try:
        parsed=urlsplit('//'+value)
        if parsed.username or parsed.password or parsed.path or not parsed.hostname:return None
        if parsed.port is not None and not 1<=parsed.port<=65535:return None
        return allowed_hostname(parsed.hostname)
    except ValueError:return None


def host_allowlist(extra=()):
    return LOOPBACK_HOSTS | frozenset(allowed_hostname(value) for value in extra)
