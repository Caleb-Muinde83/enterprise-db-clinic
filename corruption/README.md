# corruption/

This directory will hold the scripts that deliberately re-introduce each module's
problem into the clean seeded schema, so every phase starts from a reproducible
broken state rather than one created by hand.

Populated starting with the partitioning module (unpartitioned tables at scale):
- `strip_partitioning.sql` (per engine)

Then Phase 2 (indexing):
- `introduce_missing_indexes.sql` (per engine)

Then Phase 3 (schema drift):
- `introduce_schema_drift.sql` (per engine)

Empty for now — nothing to corrupt until Phase 0's clean baseline exists and is verified.
