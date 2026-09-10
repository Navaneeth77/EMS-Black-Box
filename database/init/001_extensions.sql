-- Enable PostGIS. Runs once, on first container initialisation.
--
-- Nothing in the application connects to this database yet. The extension is
-- set up in advance so that when ingestion lands, spatial types are available
-- without a migration step.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Deterministic, verifiable UUIDs for run identifiers.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$
BEGIN
    RAISE NOTICE 'EMS Black Box: PostGIS % ready.', postgis_version();
END
$$;
