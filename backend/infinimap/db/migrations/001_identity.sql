-- 001_identity.sql
--
-- The identity layer: what hardware exists.
--
-- Nothing in these tables changes once written. The practical consequence 
-- is that they are append-only.

BEGIN;

-- ---------------------------------------------------------------------------
-- Fabric
--
-- One row per InfiniBand subnet.  Management packets do not cross subnet
-- boundaries, so each subnet is discovered by its own collector and is a
-- genuinely separate namespace.
--
-- This matters for more than tidiness: the same physical node can legitimately
-- appear in two fabrics. A dual-port HCA with port 1 on one subnet and port 2
-- on another reports the same NodeGUID to both.
-- ---------------------------------------------------------------------------
CREATE TABLE fabric (
    fabric_id      INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    name           TEXT        NOT NULL UNIQUE,
    subnet_prefix  BIGINT,
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN fabric.subnet_prefix IS
    'IB subnet prefix -- the high 64 bits of every GID on this subnet. '
    'Stored as a raw 64-bit pattern and so subject to the same signedness '
    'caveat as node_guid below - the default prefix has its high bit set and '
    'therefore stores negative. Format with guid_hex(), never compare with <.';


-- ---------------------------------------------------------------------------
-- Node type
--
-- IBTA defines these as small integers (1=CA, 2=Switch, 3=Router).  Stored as
-- an enum rather than the raw integer because these tables are read directly
-- by humans.  Translation happens once at ingest.
-- ---------------------------------------------------------------------------
CREATE TYPE node_type AS ENUM ('ca', 'switch', 'router');


-- ---------------------------------------------------------------------------
-- Node
--
-- GUID storage: BIGINT holds the raw 64-bit pattern.  Postgres has no unsigned
-- 64-bit integer, so a GUID whose OUI has the high bit set (0x88..., 0xD8...)
-- stores as a NEGATIVE number.  This is not a bug and it round-trips exactly --
-- to_hex() reproduces the original -- but it does mean:
--
--   * never compare GUIDs with < or >; ordering is meaningless
--   * always format for display with guid_hex(), never with the raw value
--
-- NUMERIC(20,0) would avoid the sign entirely but is wider and slower, and the
-- only thing it buys is a comparison operator we do not want to use anyway.
-- ---------------------------------------------------------------------------
CREATE TABLE node (
    node_id         INTEGER     PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    fabric_id       INTEGER     NOT NULL REFERENCES fabric,
    node_guid       BIGINT      NOT NULL,
    node_type       node_type   NOT NULL,
    sys_image_guid  BIGINT,
    vendor_id       INTEGER,
    device_id       INTEGER,
    num_ports       SMALLINT,
    first_seen      TIMESTAMPTZ NOT NULL,

    CONSTRAINT node_natural_key UNIQUE (fabric_id, node_guid),

    -- Referenced by port's composite foreign key.
    CONSTRAINT node_id_fabric UNIQUE (node_id, fabric_id)
);

COMMENT ON COLUMN node.sys_image_guid IS
    'Shared across all ASICs in one physical chassis.';

COMMENT ON COLUMN node.vendor_id IS
    'IEEE OUI as a 24-bit integer. Note this is NOT a PCI vendor ID and there '
    'is no automated mapping between the two.';

COMMENT ON COLUMN node.first_seen IS
    'When this hardware was first observed.';

CREATE INDEX node_by_fabric ON node (fabric_id);
CREATE INDEX node_by_sys_image ON node (fabric_id, sys_image_guid)
    WHERE sys_image_guid IS NOT NULL;


-- ---------------------------------------------------------------------------
-- Port
--
-- The natural key is (node_id, port_number) and NOT the port GUID.  All data
-- ports on a switch share a single NodeGUID and have no PortGUID at all --
-- port_number is the only thing distinguishing them.
--
-- ---------------------------------------------------------------------------
CREATE TABLE port (
    port_id      INTEGER     PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    node_id      INTEGER     NOT NULL,
    fabric_id    INTEGER     NOT NULL,
    port_number  SMALLINT    NOT NULL,
    port_guid    BIGINT,
    first_seen   TIMESTAMPTZ NOT NULL,

    CONSTRAINT port_natural_key UNIQUE (node_id, port_number),

    CONSTRAINT port_number_valid CHECK (port_number BETWEEN 0 AND 254),

    CONSTRAINT port_node_fabric_agree
        FOREIGN KEY (node_id, fabric_id) REFERENCES node (node_id, fabric_id),

    CONSTRAINT port_id_fabric UNIQUE (port_id, fabric_id)
);

COMMENT ON COLUMN port.port_guid IS
    'NULL for switch data ports, which have no PortGUID. Present on HCA ports '
    'and on switch port 0 (the management port).';

COMMENT ON CONSTRAINT port_number_valid ON port IS
    'Port 0 is the switch management port; 1-254 are data ports. 255 is '
    'reserved as the all-ports selector and is never a real port.';

CREATE INDEX port_by_fabric ON port (fabric_id);
CREATE INDEX port_by_node ON port (node_id);


-- ---------------------------------------------------------------------------
-- Display helper
--
-- Formats a GUID as the 16-digit hex string everyone in the InfiniBand world
-- actually reads, handling the negative-BIGINT case transparently.
-- ---------------------------------------------------------------------------
CREATE FUNCTION guid_hex(guid BIGINT) RETURNS TEXT
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN '0x' || lpad(to_hex(guid), 16, '0');

COMMIT;