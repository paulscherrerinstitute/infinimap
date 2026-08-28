"""Stage 2: turn raw tool output into a source-neutral observation.

One subpackage per product, because the two share no machinery -- not a parser,
not a contract, not a destination table:

  topology/  saquery record dumps -> Observation   (identity + state, 001/002)
  counters/  ibqueryerrors dump   -> CounterObservation  (error + congestion, 003)
"""
