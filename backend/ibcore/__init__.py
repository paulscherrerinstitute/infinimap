"""Pure logic shared by the collector, the API and the offline exporter.

Nothing here touches a database, a socket or a file. Every module is a set of
total functions over small values, which is what makes them safe to import from
all three callers -- and testable without any of them.
"""
