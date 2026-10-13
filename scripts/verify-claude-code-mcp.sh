#!/usr/bin/env bash
# Manual, opt-in Claude Code remote MCP verification. It does not add, edit,
# or remove Claude configuration, and it never prints the bearer credential.
set -Eeuo pipefail

if [[ "${RUN_CLAUDE_CODE_E2E:-}" != "1" ]]; then
    echo "Set RUN_CLAUDE_CODE_E2E=1 to run the manual Claude Code E2E check." >&2
    exit 2
fi

: "${AGENT_CONTEXT_MCP_URL:?set AGENT_CONTEXT_MCP_URL to the full /mcp URL}"
: "${AGENT_CONTEXT_TOKEN:?set AGENT_CONTEXT_TOKEN to a dedicated Agent Context credential}"
: "${AGENT_CONTEXT_PROJECT_ID:?set AGENT_CONTEXT_PROJECT_ID to an authorized test project}"

if [[ "${AGENT_CONTEXT_MCP_URL}" != https://* && "${AGENT_CONTEXT_MCP_URL}" != http://127.0.0.1:* ]]; then
    echo "AGENT_CONTEXT_MCP_URL must use HTTPS outside local loopback development." >&2
    exit 2
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "Claude Code CLI was not found on PATH." >&2
    exit 3
fi

claude mcp get agent-context

claude -p \
    --allowedTools 'mcp__agent-context__search_knowledge' \
    'Use the Agent Context search_knowledge MCP tool to search for "Claude Code MCP verification". State whether the tool call succeeded.'

claude -p \
    --allowedTools 'mcp__agent-context__save_context' \
    "Use the Agent Context save_context MCP tool once. Set project_id to ${AGENT_CONTEXT_PROJECT_ID}, title to 'Claude Code MCP manual verification', and content to 'Manual remote HTTP MCP verification record.' State the returned context identifier."
