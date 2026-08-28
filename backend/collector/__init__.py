"""Collector for the InfiniBand fabric monitor.

Two halves per cycle, against one fabric and one shared identity cache:

  saquery       -> Observation        -> identity + state   (sql/001, 002)
  ibqueryerrors -> CounterObservation -> error + congestion (sql/003)

Topology first, because counter samples carry port_id and the topology half is
what creates port rows. Traffic is a third path on its own clock, in the same
database; see daemon.traffic_loop.
"""
