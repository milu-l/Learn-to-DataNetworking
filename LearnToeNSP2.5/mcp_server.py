import asyncio
import sys

from fastmcp import FastMCP

from mcp_tools import register_tools


def create_mcp_server():
    mcp = FastMCP(
        name="Learn to DataNetworking",
        instructions="通过 MCP 协议管理 eNSP、EVE-NG 和 PNETLab 中的网络设备，支持端口扫描、Telnet 连接、命令执行等功能"
    )
    register_tools(mcp)
    return mcp


def run_sse():
    mcp = create_mcp_server()
    print(f"\n{'='*60}")
    print(f"  Learn to DataNetworking - MCP SSE 模式")
    print(f"  MCP SSE 端点: http://0.0.0.0:5001/sse")
    print(f"  MCP 消息端点: http://0.0.0.0:5001/messages")
    print(f"{'='*60}\n")
    asyncio.run(mcp.run_async(transport="sse", host="0.0.0.0", port=5001, path="/sse", show_banner=False))


def run_stdio():
    mcp = create_mcp_server()
    asyncio.run(mcp.run_async(transport="stdio", show_banner=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        run_sse()
    else:
        run_stdio()
