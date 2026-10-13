"""Authentication, authorization, and the rules that keep secrets out of sight.

One definition of what a caller may do lives here, and every surface reads it:
the API enforces it per endpoint, the MCP server checks it before building a
request, and `contextctl` mints the keys that carry it.
"""
