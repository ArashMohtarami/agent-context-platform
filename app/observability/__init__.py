"""What the platform says about itself while it runs.

Three things live here, and they answer three different questions. The
correlation context in `context` answers *which request was that?* — one
identifier that follows a call from the MCP server into the API and through
every log line it produces. The registry in `metrics` answers *how is it
going?* — counters and latency distributions, held in memory and rendered as
Prometheus text. The command listener in `mongo` answers *how long is the
database taking?* without a repository having to remember to measure.

Everything here is self-hosted by construction. No module in this package
opens a connection, writes a file, or depends on a telemetry library: metrics
are a dictionary of numbers in the process that recorded them, and logs are
lines on standard output. A deployment that wants to collect either already has
what it needs — a scraper and whatever reads container logs.

Nothing observed here can carry content. Every metric value is a number, every
label is a bounded identifier the application chose, and the log fields are an
allow-list in `app.core.logging`. A query, a document, a memory, and a
credential are all incapable of reaching this package, which is what makes it
safe to ship what it produces somewhere else.
"""
