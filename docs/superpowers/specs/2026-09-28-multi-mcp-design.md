# Multi-MCP Agent Design

## Purpose

Extend the existing PydanticAI agent from one MCP server to multiple HTTP MCP
servers. Every configured server uses the same global `MCP_API_KEY`. At startup,
the application discovers each server independently, keeps every healthy server,
and logs each unavailable server without exposing credentials or sensitive URL
query values.

This design deliberately separates structural unit tests from live integration
tests. The expired certificate on `aic.iteyes.io` does not block configuration,
selection, prefixing, validation, or partial-failure unit tests because those
tests mock the MCP connection boundary.

## Configuration

The `.env` file contains a JSON array and one shared credential:

```dotenv
MCP_SERVERS=[{"id":"agriculture","url":"https://aic.iteyes.io:8101/mcp","prefix":"agriculture"},{"id":"law","url":"https://mcp.gomdori.app/law?oc=honggildong","prefix":"law"}]
MCP_API_KEY=Bearer shared-key
```

Each server entry has these required fields:

- `id`: stable identifier used in logs and validator ownership
- `url`: complete MCP endpoint, including any required query parameters
- `prefix`: unique prefix exposed to the model for that server's tools

`id` and `prefix` must each be non-empty and unique. URLs must use `http` or
`https`. A missing or malformed configuration is a startup configuration error.
There is no per-server API key setting in this version.

## Architecture

### Configuration and runtime records

`agent/mcp_harness.py` owns an immutable server configuration model and a parser
for `MCP_SERVERS`. It also owns a runtime record that keeps the server config,
the raw `MCPToolset`, and the prefixed wrapper together.

Keeping the raw toolset is necessary for startup discovery through
`list_tools()`. The prefixed wrapper is passed to the Agent so model-visible tool
names are unambiguous, such as `agriculture_search` and `law_search`.

### Toolset construction

For every configuration entry, the factory creates an `MCPToolset` with the
existing retry and timeout policy and this shared header:

```python
{"Authorization": os.environ["MCP_API_KEY"]}
```

The factory creates a server-bound `process_tool_call` callback. This preserves
the originating `server_id` even though a prefixed wrapper translates the
model-visible name back to the original MCP tool name before calling the raw
toolset.

### Startup discovery and Agent construction

The FastAPI lifespan performs these steps:

1. Parse and validate all MCP server configurations.
2. Build one runtime record per server.
3. Probe all raw toolsets with `list_tools()` concurrently so startup latency is
   bounded by the slowest server rather than the sum of all timeouts.
4. For every successful probe, register that server's schemas and retain its
   prefixed toolset.
5. For every failed probe, emit a sanitized failure log and exclude that server.
6. If no server succeeds, raise a startup error.
7. Build the Agent once with only the successful prefixed toolsets and store it
   in application state for the `/chat` endpoint.

The application does not periodically reconnect excluded servers in this
version. A restart repeats discovery. A server that becomes unavailable after
startup follows the existing per-request MCP error behavior.

## Schema Isolation

The current global validator map is keyed only by tool name and is cleared on
every registration. That is unsafe for multiple servers because two servers may
publish the same tool name and later registration removes earlier schemas.

Validators will instead be keyed by `(server_id, original_tool_name)`.
Registration clears only the selected server's validators before adding its
current schemas. Result validation receives the server ID from the bound tool
callback and uses the compound key. This keeps schemas isolated even when two
servers both expose a tool named `search`.

## Logging and Security

Startup logs identify servers by `id` and sanitized network location. They never
contain the authorization header, API key, URL user information, query string,
or fragment.

Example success and failure messages:

```text
MCP SERVER CONNECTED id=law host=mcp.gomdori.app tool_count=5
MCP SERVER CONNECTION FAILED id=agriculture host=aic.iteyes.io:8101 error_type=ConnectError
```

Tool-call logs will include the server ID so tools with identical original names
remain distinguishable. Existing masking of sensitive arguments remains in
effect.

## Failure Policy

- Invalid JSON, duplicate IDs or prefixes, missing required fields, and invalid
  URL schemes fail startup immediately.
- A connection, authentication, TLS, timeout, or discovery failure for one MCP
  server is logged and does not prevent other healthy servers from starting.
- If every configured server fails discovery, application startup fails.
- Failed startup logs expose the exception type but not its message, preventing
  credentials or query values embedded in exceptions from leaking.

## Code Changes

### `agent/mcp_harness.py`

- Replace the single-server factory with configuration parsing and multi-server
  runtime construction.
- Bind tool processing and schema validation to `server_id`.
- Replace the tool-name-only validator map with a compound-key map.
- Preserve existing connection policy and shared result validation rules.

### `agent/app.py`

- Remove module-level construction of a single `mcp_toolset` and Agent.
- Discover MCP servers during lifespan.
- Construct and store an Agent containing only healthy prefixed toolsets.
- Read the initialized Agent from application state in `/chat`.

### `agent/logging_utils.py`

- Add sanitized MCP server connection success and failure logs.
- Add server identity to MCP tool execution logs without exposing secrets.

### `.env`

- Replace `MCP_SERVER_URL` with `MCP_SERVERS`.
- Keep one global `MCP_API_KEY`.

### Tests

- Update existing single-server configuration and lifespan tests.
- Add parser tests for valid configuration, malformed JSON, invalid URLs, and
  duplicate IDs or prefixes.
- Assert every constructed toolset receives the same authorization value.
- Assert prefixes produce distinct model-visible tool names.
- Mock discovery to cover all-success, partial-success, and all-failed startup.
- Verify schema isolation for identical tool names on different servers.
- Verify failure logs identify the server while excluding keys and URL query
  values.

No unit test makes a network connection. A separate live integration check may
be run after the remote TLS certificate is renewed, but it is not required for
the structural implementation.

## Acceptance Criteria

- Two or more MCP servers can be declared through `MCP_SERVERS`.
- The global `MCP_API_KEY` is applied to every configured server.
- Tool names are prefixed per server and do not collide.
- One healthy server is sufficient for successful application startup.
- Every unavailable server produces a sanitized, identifiable log entry.
- Zero healthy servers causes a clear startup failure.
- Output schemas remain isolated by server and tool name.
- The complete unit suite passes without contacting a live MCP server.

## Non-Goals

- Per-server credentials
- Runtime addition or removal of server configurations
- Periodic health checks or automatic reconnection of startup-excluded servers
- Disabling TLS verification
- Repairing or replacing remote server certificates
