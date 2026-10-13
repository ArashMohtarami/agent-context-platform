"""Client-aware MCP gateway middleware.

The modules here orchestrate identity, policy, and operational reporting.  The
package intentionally does not re-export them: the HTTP client depends on the
request-context module, so eager re-exports would create an import cycle.
"""
