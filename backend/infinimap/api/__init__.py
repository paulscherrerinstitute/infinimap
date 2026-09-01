"""Read-only HTTP API over the identity + state schema.

Serves the fabric to the frontend: the lean graph, per-selection detail, the
snapshot index, the event feed, and diffs between two points in time. The
collector remains the only writer - nothing here issues anything but SELECT.
"""
