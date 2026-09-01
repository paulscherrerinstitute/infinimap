"""infinimap - a live, time-aware map of an InfiniBand fabric.

Three packages, in pipeline order:

  collector   acquires the fabric via saquery/ibqueryerrors and writes
              temporally versioned records
  api         serves that history over HTTP, time-aware
  ibcore      the decode/health/identity primitives both halves share
"""

__version__ = "0.1.0"
