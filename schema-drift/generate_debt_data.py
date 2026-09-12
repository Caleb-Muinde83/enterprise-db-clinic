#!/usr/bin/env python3
"""
generate_debt_data.py — Produces the two debt datasets for the schema-drift module:

  addresses.csv        — one address per customer (customer_id, street, city,
                          state, zip, country). Loaded into a staging table by
                          the corruption scripts, then used both to populate
                          orders.shipping_address (denormalized, redundant) and
                          later to build the proper `addresses` table (fix).

  customer_notes.csv    — note_id, customer_id (as a STRING — the deliberate
                          type-drift problem), note_text, created_at. A
                          fraction of rows have a malformed (non-numeric)
                          customer_id, and another fraction have a numeric but
                          nonexistent customer_id (orphaned) — both deliberate.

Scale presets match seed/generate_data.py's customer counts, so the addresses
file always has exactly one row per real customer.

Usage:
    python generate_debt_data.py --scale full --out ../data
"""

import argparse
import csv
import random
from datetime import datetime, timedelta

from faker import Faker

# Mirrors seed/generate_data.py's SCALE_PRESETS customer counts, so addresses.csv
# always has exactly one row per real customer at whatever scale is loaded.
CUSTOMER_COUNTS = {"small": 1_000, "medium": 50_000, "full": 500_000}

NOTES_PER_10_CUSTOMERS = 3  # roughly 30% of customers have 1 CRM note
MALFORMED_FRACTION = 0.05   # non-numeric customer_id
ORPHANED_FRACTION = 0.05    # numeric but nonexistent customer_id

NOTE_TEMPLATES = [
    "Called about {topic}, resolved.",
    "Follow-up needed on {topic}.",
    "Customer requested info on {topic}.",
    "Complaint logged regarding {topic}.",
    "Escalated to tier 2 — {topic}.",
]
TOPICS = ["shipping delay", "refund request", "account access", "damaged item",
          "billing question", "product inquiry", "loyalty program", "return"]


def generate_addresses(n_customers, out_dir, fake, rng):
    path = f"{out_dir}/addresses.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_id", "street", "city", "state", "zip", "country"])
        for cid in range(1, n_customers + 1):
            writer.writerow([
                cid, fake.street_address(), fake.city(),
                fake.state_abbr() if rng.random() < 0.7 else fake.state(),
                fake.postcode(), fake.country(),
            ])
    print(f"Wrote {n_customers:,} addresses to {path}")


def generate_customer_notes(n_customers, out_dir, fake, rng):
    n_notes = int(n_customers * NOTES_PER_10_CUSTOMERS / 10)
    path = f"{out_dir}/customer_notes.csv"
    start = datetime(2023, 9, 1)
    end = datetime(2026, 9, 1)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["note_id", "customer_id", "note_text", "created_at"])

        note_id = 0
        for _ in range(n_notes):
            note_id += 1
            r = rng.random()
            if r < MALFORMED_FRACTION:
                # Deliberately malformed: a non-numeric string, e.g. a typo'd
                # ID or a stray identifier from some other system entirely.
                customer_id_str = f"CUST-{rng.randint(1000, 9999)}"
            elif r < MALFORMED_FRACTION + ORPHANED_FRACTION:
                # Deliberately orphaned: numeric-looking, but well beyond the
                # real customer_id range, so it can never match.
                customer_id_str = str(n_customers + rng.randint(100_000, 999_999))
            else:
                customer_id_str = str(rng.randint(1, n_customers))

            topic = rng.choice(TOPICS)
            note_text = rng.choice(NOTE_TEMPLATES).format(topic=topic)
            delta = end - start
            created_at = start + timedelta(seconds=rng.random() * delta.total_seconds())

            writer.writerow([note_id, customer_id_str, note_text,
                              created_at.isoformat(sep=" ")])

    print(f"Wrote {n_notes:,} customer_notes to {path} "
          f"({MALFORMED_FRACTION*100:.0f}% malformed, {ORPHANED_FRACTION*100:.0f}% orphaned)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=CUSTOMER_COUNTS.keys(), default="full",
                         help="Must match the scale already loaded in the database")
    parser.add_argument("--out", default=".")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    n_customers = CUSTOMER_COUNTS[args.scale]
    fake = Faker()
    Faker.seed(args.seed)
    rng = random.Random(args.seed)

    print(f"Generating debt data for {args.scale} scale ({n_customers:,} customers)")
    generate_addresses(n_customers, args.out, fake, rng)
    generate_customer_notes(n_customers, args.out, fake, rng)


if __name__ == "__main__":
    main()
