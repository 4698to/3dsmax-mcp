# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxmcp.max_client import MaxClient

for host, port in (("127.0.0.1", 8765), ("localhost", 8765), ("192.168.139.45", 8765)):
    c = MaxClient(host=host, port=port, transport="tcp")
    try:
        r = c.send_command('"pong"', cmd_type="maxscript", timeout=5)
        print("ok", host, port, r.get("result"))
    except Exception as e:
        print("fail", host, port, type(e).__name__, e)
