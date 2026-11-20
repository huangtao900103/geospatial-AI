#!/usr/bin/env python3
"""Container entry point. The data volume is independent of the image."""
import os
import sys
from pathlib import Path


def command(environ,root):
    # Keep validation shared with the HTTP handler, not a shell allowlist.
    from backend.core.network_policy import host_allowlist
    extra=[value.strip() for value in environ.get('GEOAI_ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if value.strip()]
    hosts=host_allowlist(extra)
    args=[sys.executable,str(root/'server.py'),'--bind','0.0.0.0','--port','8016','--db',str(root/'data'/'workspace.sqlite3')]
    for host in sorted(hosts):args+=['--allowed-host',host]
    return args


def main():
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root))
    try:args=command(os.environ,root)
    except ValueError as error:raise SystemExit('Invalid deployment configuration: '+str(error))
    target=root/'data';target.mkdir(parents=True,exist_ok=True)
    if not os.access(target,os.W_OK):raise SystemExit('The workspace data volume is not writable by UID 10001; check restored-file ownership.')
    os.execv(sys.executable,args)


if __name__=='__main__':main()
