"""The MCP server exposing the knowledge platform to agents.

This package is a translation layer and nothing else. It turns an MCP tool call
into one Context API request and shapes the answer for a model to read. It
opens no database connection, ranks nothing, embeds nothing, and decides
nothing about what a project may see — every one of those lives behind the API,
in one place, where it is already tested.
"""
