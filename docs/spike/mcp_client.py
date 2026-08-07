"""Minimal stdlib-only Python client for the TLA+ MCP server (streamable HTTP, stateless)."""
import json
import urllib.request

URL = "http://127.0.0.1:8931/mcp"
PROTOCOL = "2025-06-18"


def rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read().decode()
    # Streamable HTTP replies as SSE: pull the JSON out of `data:` lines.
    if "text/event-stream" in resp.headers.get("Content-Type", ""):
        out = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:].strip())
                if msg.get("id") == rid:      # skip keep-alive log notifications
                    out = msg
        return out
    return json.loads(raw)


def initialize():
    return rpc("initialize", {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "aperture-spike", "version": "0.0.1"},
    })


def call_tool(name, args, rid=99):
    return rpc("tools/call", {"name": name, "arguments": args}, rid=rid)


def text_of(result):
    if not result or "result" not in result:
        return f"<no result: {result}>"
    return "\n".join(
        c.get("text", "") for c in result["result"].get("content", [])
    )


if __name__ == "__main__":
    import sys

    init = initialize()
    print("=== initialize ===")
    print(json.dumps(init.get("result", init), indent=1)[:400])

    tools = rpc("tools/list", {}, rid=2)
    names = [t["name"] for t in tools["result"]["tools"]]
    print(f"\n=== tools/list ({len(names)}) ===")
    for n in names:
        print(" ", n)

    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    print("\n=== sany_parse on a VALID spec ===")
    print(text_of(call_tool("tlaplus_mcp_sany_parse", {"fileName": f"{ws}/Spike.tla"})))

    print("\n=== sany_parse on a BROKEN spec ===")
    print(text_of(call_tool("tlaplus_mcp_sany_parse", {"fileName": f"{ws}/Broken.tla"})))

    print("\n=== tlc_check (expect invariant violation + trace) ===")
    print(text_of(call_tool(
        "tlaplus_mcp_tlc_check",
        {"fileName": f"{ws}/Spike.tla", "cfgFile": f"{ws}/Spike.cfg"},
    )))
