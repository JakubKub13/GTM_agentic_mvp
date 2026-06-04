#!/bin/sh
# Langfuse MCP bridge for Codex CLI.
#
# Codex's native streamable-HTTP (rmcp) client cannot talk to the Langfuse MCP
# server (Langfuse returns application/json and requires Accept of BOTH
# application/json and text/event-stream; rmcp fails to deserialize the reply).
# So we bridge through `mcp-remote` (stdio <-> streamable HTTP), which handles
# the transport correctly.
#
# The Basic credential is NOT stored here or in config.toml: it is read from
# .env at launch (LANGFUSE_MCP_AUTH = "Basic base64(pk:sk)").
set -e

ENV_FILE="/Users/phantomghost/Desktop/GTM-duvo/duvo-signal-loop/.env"
set -a
. "$ENV_FILE"
set +a

# Ensure npx is reachable even when Codex's subprocess has a minimal PATH.
NODE_BIN="/Users/phantomghost/.nvm/versions/node/v22.20.0/bin"
[ -x "$NODE_BIN/npx" ] && export PATH="$NODE_BIN:$PATH"

exec npx -y mcp-remote https://cloud.langfuse.com/api/public/mcp \
  --header "Authorization: ${LANGFUSE_MCP_AUTH}" \
  --transport http-only
