import re

with open("migrate.py", "r", encoding="utf-8") as f:
    content = f.read()

new_function = '''def fix_customer_notes(engine, run):
    fk_result = run(FK_EXISTS_CHECK[engine])
    nums = [int(n) for n in fk_result.stdout.split() if n.strip().isdigit()]
    if nums and nums[0] > 0:
        print("customer_notes FK already exists -- already fixed for this engine, skipping.")
        return

    print("Archiving malformed/orphaned customer_notes rows...")
    if engine == "postgres":
        steps = [
            """CREATE TABLE IF NOT EXISTS customer_notes_orphaned_archive (LIKE customer_notes INCLUDING ALL);""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id !~ '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id::text = cn.customer_id);""",
            """DELETE FROM customer_notes cn
               WHERE cn.customer_id !~ '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id::text = cn.customer_id);""",
            """ALTER TABLE customer_notes ALTER COLUMN customer_id TYPE BIGINT USING customer_id::bigint;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]
    elif engine == "mysql":
        # IMPORTANT: cast cn.customer_id (non-indexed) rather than c.customer_id
        # (customers' PK). The original version cast c.customer_id, wrapping the
        # indexed column and making the NOT EXISTS non-sargable -- for 150K
        # customer_notes rows against 500K customers, MySQL 5.7's optimizer chose
        # a nested-loop plan that ran for 6+ hours before being killed (unlike
        # Postgres, whose planner built an efficient hash anti-join despite the
        # same non-sargable pattern -- a genuine cross-engine optimizer difference).
        # CAST(... AS UNSIGNED) is safe here even for malformed rows like
        # 'CUST-1234': MySQL's lenient string-to-number conversion yields 0 for
        # non-numeric-leading strings, which never matches a real customer_id
        # (they start at 1), so malformed rows still correctly end up flagged.
        steps = [
            """CREATE TABLE IF NOT EXISTS customer_notes_orphaned_archive LIKE customer_notes;""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id NOT REGEXP '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = CAST(cn.customer_id AS UNSIGNED));""",
            """DELETE FROM customer_notes
               WHERE customer_id NOT REGEXP '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = CAST(customer_notes.customer_id AS UNSIGNED));""",
            """ALTER TABLE customer_notes MODIFY COLUMN customer_id BIGINT NOT NULL;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]
    else:  # sqlserver
        # Same fix as MySQL: cast the non-indexed cn.customer_id side, leave
        # customers.customer_id (PK) bare so the index stays usable. TRY_CAST
        # (not plain CAST) is required here -- unlike MySQL's lenient conversion,
        # SQL Server's CAST throws a hard error on non-numeric input like
        # 'CUST-1234'; TRY_CAST returns NULL instead, which safely never matches
        # via the equality comparison, correctly leaving malformed rows flagged.
        steps = [
            """IF OBJECT_ID('dbo.customer_notes_orphaned_archive','U') IS NULL
               SELECT * INTO customer_notes_orphaned_archive FROM customer_notes WHERE 1=0;""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id LIKE '%[^0-9]%'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = TRY_CAST(cn.customer_id AS BIGINT));""",
            """DELETE FROM customer_notes
               WHERE customer_id LIKE '%[^0-9]%'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = TRY_CAST(customer_notes.customer_id AS BIGINT));""",
            """ALTER TABLE customer_notes ALTER COLUMN customer_id BIGINT NOT NULL;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]

    for i, sql in enumerate(steps, 1):
        check(run(sql), f"customer_notes fix step {i}")
    print("customer_notes fixed: archived, retyped, FK added.")


'''

pattern = re.compile(r"def fix_customer_notes\(.*?\n\n\n", re.DOTALL)
new_content, count = pattern.subn(new_function, content, count=1)

if count == 0:
    print("ERROR: could not find fix_customer_notes function to replace. "
          "No changes made. Check migrate.py manually.")
else:
    with open("migrate.py", "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Patched successfully ({count} replacement made).")

with open("migrate.py", "r", encoding="utf-8") as f:
    verify = f.read()
if "CAST(customer_notes.customer_id AS UNSIGNED)" in verify:
    print("VERIFIED: fix is now present in migrate.py")
else:
    print("WARNING: fix still not found after patching -- something went wrong")
