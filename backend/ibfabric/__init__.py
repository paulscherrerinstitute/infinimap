"""ibfabric — parse ibdiagnet2 snapshots into a fabric graph."""

from .model import (
    Cable,
    FabricGraph,
    Link,
    LinkCheckEvent,
    LinkHealth,
    Node,
    NodeType,
    Port,
    PortCounters,
    PortState,
    SmInfo,
    Switch,
)
from .parse import build_graph, read_sections
from .serialize import edge_id, to_dict

__all__ = [
    "build_graph",
    "read_sections",
    "to_dict",
    "edge_id",
    "FabricGraph",
    "Node",
    "Port",
    "Link",
    "PortCounters",
    "Cable",
    "Switch",
    "SmInfo",
    "LinkCheckEvent",
    "NodeType",
    "PortState",
    "LinkHealth",
]
