import re

with open("migrate.py", "r", encoding="utf-8") as f:
    content = f.read()

new_function = '''def build_order_address_map(engine, run):
    """Parses shipping_address exactly ONCE per row (not five times, as the
    original per-batch UPDATE did) into a lightweight order_id -> address_id
    mapping table. The batched backfill then joins against this via a plain
    BIGINT equality on order_id, instead of matching five TEXT columns
    against freshly-reparsed JSON on every row of every batch -- the original
    approach took ~3 hours for 3M rows on Postgres; this reduces the JSON
    parsing work by 5x and makes the batched join itself far cheaper."""
    e = JSON_EXTRACT[engine]
    print("Building order_id -> address_id mapping (parses JSON once per row)...")

    if engine == "postgres":
        steps = [
            "DROP TABLE IF EXISTS order_address_map;",
            """CREATE TABLE order_address_map (order_id BIGINT PRIMARY KEY, address_id BIGINT NOT NULL);""",
            f"""
                INSERT INTO order_address_map (order_id, address_id)
                SELECT p.order_id, a.address_id
                FROM (SELECT order_id, shipping_address::jsonb AS j FROM orders
                      WHERE shipping_address IS NOT NULL) p
                JOIN addresses a
                  ON a.street = p.j->>'street' AND a.city = p.j->>'city' AND a.state = p.j->>'state'
                 AND a.zip = p.j->>'zip' AND a.country = p.j->>'country';
            """,
        ]
    elif engine == "mysql":
        # IMPORTANT: convert only the JSON-extracted (right-hand, non-indexed) side.
        # An earlier version wrapped BOTH sides in CONVERT/COLLATE, which also
        # wrapped addresses' own indexed columns -- making the join non-sargable
        # and forcing a full unindexed nested-loop scan (3M x 442K rows), which
        # ran for 3+ hours with no end in sight before being killed. Leaving
        # a.street/a.city/etc. bare lets MySQL still use the uq_address index,
        # while converting the JSON side alone still fixes the original
        # collation error (JSON extraction defaults to utf8mb4_bin; addresses'
        # columns inherited the server's default latin1_swedish_ci).
        steps = [
            "DROP TABLE IF EXISTS order_address_map;",
            """CREATE TABLE order_address_map (order_id BIGINT PRIMARY KEY, address_id BIGINT NOT NULL) ENGINE=InnoDB;""",
            """
                INSERT INTO order_address_map (order_id, address_id)
                SELECT p.order_id, a.address_id
                FROM (SELECT order_id, shipping_address AS j FROM orders
                      WHERE shipping_address IS NOT NULL) p
                JOIN addresses a
                  ON a.street = CONVERT(p.j->>'$.street' USING latin1) COLLATE latin1_swedish_ci
                 AND a.city = CONVERT(p.j->>'$.city' USING latin1) COLLATE latin1_swedish_ci
                 AND a.state = CONVERT(p.j->>'$.state' USING latin1) COLLATE latin1_swedish_ci
                 AND a.zip = CONVERT(p.j->>'$.zip' USING latin1) COLLATE latin1_swedish_ci
                 AND a.country = CONVERT(p.j->>'$.country' USING latin1) COLLATE latin1_swedish_ci;
            """,
        ]
    else:  # sqlserver
        steps = [
            "IF OBJECT_ID('dbo.order_address_map','U') IS NOT NULL DROP TABLE order_address_map;",
            """CREATE TABLE order_address_map (order_id BIGINT PRIMARY KEY, address_id BIGINT NOT NULL);""",
            """
                INSERT INTO order_address_map (order_id, address_id)
                SELECT p.order_id, a.address_id
                FROM (SELECT order_id, shipping_address AS j FROM orders
                      WHERE shipping_address IS NOT NULL) p
                CROSS APPLY (SELECT JSON_VALUE(p.j,'$.street') AS street, JSON_VALUE(p.j,'$.city') AS city,
                                     JSON_VALUE(p.j,'$.state') AS state, JSON_VALUE(p.j,'$.zip') AS zip,
                                     JSON_VALUE(p.j,'$.country') AS country) pj
                JOIN addresses a
                  ON a.street = pj.street AND a.city = pj.city AND a.state = pj.state
                 AND a.zip = pj.zip AND a.country = pj.country;
            """,
        ]

    for i, sql in enumerate(steps, 1):
        check(run(sql), f"build order_address_map step {i}")
    print("Mapping table built.")


'''

pattern = re.compile(r"def build_order_address_map\(.*?\n\n\n", re.DOTALL)
new_content, count = pattern.subn(new_function, content, count=1)

if count == 0:
    print("ERROR: could not find build_order_address_map function to replace. "
          "No changes made. Check migrate.py manually.")
else:
    with open("migrate.py", "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Patched successfully ({count} replacement made).")

# Verify
with open("migrate.py", "r", encoding="utf-8") as f:
    verify = f.read()
if "latin1_swedish_ci" in verify:
    print("VERIFIED: fix is now present in migrate.py")
else:
    print("WARNING: fix still not found after patching -- something went wrong")