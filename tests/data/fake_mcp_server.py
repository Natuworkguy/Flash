"""A small MCP server over standard input and output, for the tests: two
tools, one read-only, one that fails, and one that gives an image."""

import json
import sys

TOOLS = [
    {"name": "add", "description": "Add two numbers.",
     "inputSchema": {"type": "object", "properties": {
         "a": {"type": "integer"}, "b": {"type": "integer"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "note", "description": "Write a note.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}}}},
    {"name": "fail", "description": "Always fails."},
    {"name": "pixel", "description": "A tiny image.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def answer(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"}}
    if method == "tools/list":
        # Two pages, to be followed.
        if params.get("cursor") == "more":
            return {"tools": TOOLS[2:]}
        return {"tools": TOOLS[:2], "nextCursor": "more"}
    if method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "add":
            return {"content": [{"type": "text",
                                 "text": str(args["a"] + args["b"])}]}
        if name == "note":
            return {"content": [{"type": "text", "text": f"noted {args}"}]}
        if name == "pixel":
            return {"content": [{"type": "image", "data": "aGk=",
                                 "mimeType": "image/png"}]}
        return {"content": [{"type": "text", "text": "it broke"}],
                "isError": True}
    return None


print("a stray line on standard output", flush=True)
print("starting up", file=sys.stderr, flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message.get("method") == "boom":
        sys.exit(3)
    result = answer(message)
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    if result is None:
        reply["error"] = {"code": -32601, "message": "no such method"}
    else:
        reply["result"] = result
    print(json.dumps(reply), flush=True)
