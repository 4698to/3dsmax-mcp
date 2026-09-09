"""Probe: fileIn mcp_server_v2.ms into max-8766, verify server+UI up."""
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


async def call(session, tool, args):
    res = await session.call_tool(tool, args)
    return " ".join(c.text for c in res.content if hasattr(c, "text"))


async def main():
    async with streamable_http_client(MCP_URL) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("ACQUIRE:", await call(session, "acquire_instance", {"name": "max-8766"}))
            try:
                r = await call(session, "execute_maxscript",
                               {"code": 'fileIn "G:/UGit/3dsmax-mcp/maxscript/mcp_server_v2.ms"'})
                print("fileIn v2       :", r.strip())
                r = await call(session, "execute_maxscript",
                               {"code": '"isRunning=" + (MCP_Server.isRunning as string) + " port=" + (MCP_Server.port as string) + " bindIP=" + MCP_Server.bindIP'})
                print("server state    :", r.strip())
                r = await call(session, "execute_maxscript",
                               {"code": '"uiForm=" + (MCP_UI.form != undefined as string) + " visible=" + (MCP_UI.form.Visible as string)'})
                print("ui state        :", r.strip())
                r = await call(session, "execute_maxscript", {"code": '"ping"', "type": "ping"})
                print("ping            :", r.strip())
            finally:
                print("RELEASE:", await call(session, "release_instance", {"name": "max-8766"}))


if __name__ == "__main__":
    asyncio.run(main())
