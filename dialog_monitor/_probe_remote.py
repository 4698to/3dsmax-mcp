# -*- coding: utf-8 -*-
from maxmcp.max_client import MaxClient

c = MaxClient(host="192.168.139.45", port=8765, transport="tcp")
print("host", c.host, "port", c.port, "transport", c.transport)
try:
    r = c.send_command('{"pong":true}', cmd_type="ping")
    print("ping", r)
except Exception as e:
    print(type(e).__name__, e)
