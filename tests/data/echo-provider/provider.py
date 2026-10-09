"""A provider for Flash's tests, speaking the provider protocol."""

import json
import sys

request = json.load(sys.stdin)

if request["action"] == "models":
    print(json.dumps({"models": [
        {"name": "echo-1", "capabilities": ["completion", "tools"],
         "context": 8192},
        "plain",
    ]}))
    sys.exit(0)

last = request["messages"][-1]
said = last.get("content", "")

if said == "fail":
    print(json.dumps({"error": "out of credit"}))
elif said == "crash":
    sys.stderr.write("it broke\n")
    sys.exit(2)
elif said == "call tool" and request.get("tools"):
    # Arguments as a JSON string, as some APIs give them.
    call = {"name": "get_date", "arguments": "{\"when\": \"now\"}"}
    print(json.dumps({
        "message": {"role": "assistant", "content": "",
                    "tool_calls": [{"function": call}]},
        "eval_count": 3,
    }))
elif request.get("stream"):
    for word in ["Hello", " from", " echo"]:
        print(json.dumps({"message": {"content": word}, "done": False}),
              flush=True)
    print(json.dumps({"done": True, "eval_count": 3}))
else:
    images = last.get("images") or []
    heard = f"{request['model']} heard: {said}"
    if images:
        heard += f" and {len(images)} image"
    # Over several lines, as one document: read whole at the end.
    print(json.dumps({
        "message": {"role": "assistant", "content": heard},
        "prompt_eval_count": 10, "eval_count": 4,
    }, indent=2))
