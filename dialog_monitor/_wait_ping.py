# -*- coding: utf-8 -*-
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxmcp.max_client import MaxClient

for i in range(15):
    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp")
    try:
        r = c.send_command('"pong"', cmd_type="maxscript", timeout=5)
        print("ok", i, r.get("result"))
        raise SystemExit(0)
    except Exception as e:
        print("fail", i, type(e).__name__, e)
        time.sleep(2)
raise SystemExit(2)
