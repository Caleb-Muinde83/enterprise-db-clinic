# Curriculum

See the full module specification (objectives, starting states, diagnosis steps, fixes,
success metrics, and deliverables for every phase) in the project planning doc:
`enterprise-db-problems-curriculum.md` (delivered alongside this repo).

## Confirmed decisions

1. **Dataset domain:** E-commerce/fintech hybrid.
2. **Scale:** ~10-20M rows in the largest table (`events`).
3. **Phase order:** 0 → 1 (partitioning) → 2 (indexing) → 3 (schema drift) →
   4 (zero-downtime) → 5 (version migration) → 6 (cross-engine comparison).
   Version migration runs last, against a schema that already carries every other fix.
4. **Zero-downtime scope:** Implemented for all three flagship engines (PostgreSQL,
   MySQL, SQL Server), with the cross-engine comparison itself as a deliverable.
