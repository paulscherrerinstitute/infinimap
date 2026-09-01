"""ibqueryerrors dump -> CounterObservation.

parse (text -> per-port readings) -> assemble (readings -> CounterObservation).

There is no encode stage: ibqueryerrors prints counter values as plain decimal
integers under their IBTA field names, so there is nothing to decode.
"""
