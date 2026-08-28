"""Stage 3: fold an observation into the database.

  writer.py    Observation -> identity + state (change-gate, diff, write)
  counters.py  CounterObservation -> baseline, deltas, sparse samples
  identity.py  the (guid, port) -> port_id cache both writers resolve through
"""
