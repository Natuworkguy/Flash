---
name: connect-an-app
description: Get a tool Flash does not have: connect an MCP server or install an extension
---
When a task needs an app Flash cannot reach (GitHub, a database, Notion, a company API):

1. MCP server: request_connector with a name and target: its command (npx -y @modelcontextprotocol/server-github, python server.py) or its URL (http://localhost:1234/mcp). For a local server that must be started first, give its URL as target and its command as start. Never put a secret in target: say which key it needs and the user adds it in Settings, Connectors.
2. Extension: request_extension with github@owner/repo (#branch), git@<url> or path@/folder, for slash commands, tools, model providers, themes or guides.
3. Once added, refresh_tools if the new tools are not there yet, then use them. It also reads Flash's own tools again (an extension installed or changed, email or the computer set up) and, after the servers, lists only the Flash tools added, removed or updated, nothing if none changed. A spark's shift does this by itself as it starts, so a new or changed app's tools reach sparks on their next shift. To bring them to every spark at once, the user clicks Refresh tools at the top of the Sparks page: it does what refresh_tools does, in Flash and in the process running sparks' shifts. A change to Flash's own code still needs Flash restarted.

The user is asked first unless UltraAuto is on. In the terminal: /mcp add NAME TARGET [--start COMMAND], /extension install SOURCE.

## Examples

A server Flash runs.
```
request_connector {"name": "github", "target": "npx -y @modelcontextprotocol/server-github", "why": "To read the issues you mentioned. It needs GITHUB_PERSONAL_ACCESS_TOKEN, added in Settings, Connectors."}
```

A local server started first, then reached at its URL.
```
request_connector {"name": "mine", "target": "http://localhost:1234/mcp", "start": "python mymcp/server.py", "why": "Your server's tools."}
```

```
request_extension {"source": "github@acme/flash-jira", "why": "Adds Jira tools for the tickets you track."}
```

```
refresh_tools {}
```
