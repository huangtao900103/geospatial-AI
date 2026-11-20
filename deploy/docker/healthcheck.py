#!/usr/bin/env python3
"""Local application readiness without model calls or external service checks."""
import json
import sys
from urllib.request import urlopen


def main():
    try:
        with urlopen('http://127.0.0.1:8016/api/agri-operations/catalog',timeout=3) as response:
            value=json.load(response)
            if response.status!=200 or value.get('schema_version')!='agriculture-operations-1.0':return 1
        return 0
    except Exception:return 1


if __name__=='__main__':sys.exit(main())
