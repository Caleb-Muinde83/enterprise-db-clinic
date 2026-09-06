#!/usr/bin/env python3
"""
generate_data.py — Synthetic e-commerce/fintech dataset generator for enterprise-db-clinic.

Produces CSVs for: customers, products, orders, order_items, payments, events.
`events` is the large, time-ordered table every curriculum module's problems center on.

Design choices (deliberate, not arbitrary):
  - Customer activity is skewed (Zipf-like), not uniform — a small fraction of customers
    account for a large fraction of orders/events, matching real traffic patterns.
  - event_time spans multiple years so time-based partitioning is meaningful.
  - Status/category fields use a small fixed cardinality (4-6 values), not high-cardinality
    noise, so indexing decisions (Phase 2) have a realistic selectivity profile to reason about.
  - Written straight to CSV rather than inserted row-by-row over a DB connection, since
    bulk-loading via each engine's native tool (COPY / LOAD DATA INFILE / BULK INSERT) is
    the only realistic way to land 10-20M rows in reasonable time — and it's also how a
    real migration/ETL job would do it.

Usage:
    python generate_data.py --scale small --out ../data
    python generate_data.py --scale full --out ../data --seed 42
"""

import argparse
import csv
import json
import os
import random
from datetime import datetime, timedelta

import numpy as np
from faker import Faker
from tqdm import tqdm

SCALE_PRESETS = {
    # customers, products, orders, events
    "small":  dict(customers=1_000,   products=500,   orders=5_000,    events=100_000),
    "medium": dict(customers=50_000,  products=5_000,  orders=250_000,  events=2_000_000),
    "full":   dict(customers=500_000, products=20_000, orders=3_000_000, events=15_000_000),
}

CUSTOMER_TIERS = ["bronze", "silver", "gold", "platinum"]
CUSTOMER_TIER_WEIGHTS = [0.55, 0.25, 0.15, 0.05]

PRODUCT_CATEGORIES = ["electronics", "apparel", "home", "beauty", "sports", "books", "toys"]

ORDER_STATUSES = ["pending", "paid", "shipped", "cancelled", "refunded"]
ORDER_STATUS_WEIGHTS = [0.05, 0.15, 0.65, 0.10, 0.05]

PAYMENT_METHODS = ["card", "paypal", "bank_transfer", "wallet"]
PAYMENT_STATUSES = ["success", "failed", "refunded"]
PAYMENT_STATUS_WEIGHTS = [0.90, 0.06, 0.04]

EVENT_TYPES = ["page_view", "search", "add_to_cart", "remove_from_cart",
               "checkout_start", "checkout_complete", "login", "logout"]
EVENT_TYPE_WEIGHTS = [0.45, 0.15, 0.12, 0.04, 0.06, 0.05, 0.08, 0.05]

DATE_RANGE_YEARS = 3
CHUNK_SIZE = 100_000  # rows buffered before flushing to disk


def daterange_start_end():
    end = datetime(2026, 9, 1)
    start = end - timedelta(days=365 * DATE_RANGE_YEARS)
    return start, end


def random_datetime_between(start, end, rng):
    delta = end - start
    seconds = rng.random() * delta.total_seconds()
    return start + timedelta(seconds=seconds)


def zipf_weights(n, rng, a=0.8):
    """Zipf-like weights over n items — a small fraction dominate the mass,
    but not to an unrealistic degree (a=0.8 keeps even the top customer's
    share in the low single digits at full scale, not 20-30%)."""
    ranks = np.arange(1, n + 1)
    weights = 1.0 / np.power(ranks, a)
    rng.shuffle(weights)  # so it's not correlated with customer_id order
    return weights / weights.sum()


def write_csv(path, header, rows_iter, total):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in tqdm(rows_iter, total=total, desc=os.path.basename(path)):
            writer.writerow(row)


def generate_customers(n, out_dir, fake, rng, np_rng):
    start, end = daterange_start_end()
    path = os.path.join(out_dir, "customers.csv")

    def rows():
        for cid in range(1, n + 1):
            tier = np_rng.choice(CUSTOMER_TIERS, p=CUSTOMER_TIER_WEIGHTS)
            yield [
                cid,
                fake.unique.email() if n <= 200_000 else f"customer{cid}@example.com",
                fake.first_name(),
                fake.last_name(),
                fake.country(),
                tier,
                random_datetime_between(start, end, rng).isoformat(sep=" "),
            ]

    write_csv(path, ["customer_id", "email", "first_name", "last_name",
                      "country", "customer_tier", "created_at"], rows(), n)


def generate_products(n, out_dir, fake, rng, np_rng):
    start, end = daterange_start_end()
    path = os.path.join(out_dir, "products.csv")

    def rows():
        for pid in range(1, n + 1):
            category = np_rng.choice(PRODUCT_CATEGORIES)
            yield [
                pid,
                f"SKU-{pid:08d}",
                fake.catch_phrase(),
                category,
                round(rng.uniform(4.99, 899.99), 2),
                random_datetime_between(start, end, rng).isoformat(sep=" "),
            ]

    write_csv(path, ["product_id", "sku", "name", "category", "price", "created_at"],
               rows(), n)


def generate_orders_items_payments(n_orders, n_customers, n_products, out_dir, rng, np_rng):
    start, end = daterange_start_end()
    orders_path = os.path.join(out_dir, "orders.csv")
    items_path = os.path.join(out_dir, "order_items.csv")
    payments_path = os.path.join(out_dir, "payments.csv")

    customer_weights = zipf_weights(n_customers, np_rng)  # skewed: some customers order far more

    order_item_id = 0
    payment_id = 0

    with open(orders_path, "w", newline="", encoding="utf-8") as of, \
         open(items_path, "w", newline="", encoding="utf-8") as itf, \
         open(payments_path, "w", newline="", encoding="utf-8") as pf:

        ow = csv.writer(of)
        iw = csv.writer(itf)
        pw = csv.writer(pf)

        ow.writerow(["order_id", "customer_id", "order_date", "status", "total_amount"])
        iw.writerow(["order_item_id", "order_id", "product_id", "quantity", "unit_price"])
        pw.writerow(["payment_id", "order_id", "payment_date", "amount", "method", "status"])

        customer_ids = np_rng.choice(
            np.arange(1, n_customers + 1), size=n_orders, p=customer_weights
        )

        for order_id in tqdm(range(1, n_orders + 1), total=n_orders, desc="orders.csv"):
            customer_id = int(customer_ids[order_id - 1])
            order_date = random_datetime_between(start, end, rng)
            status = np_rng.choice(ORDER_STATUSES, p=ORDER_STATUS_WEIGHTS)

            n_items = rng.randint(1, 4)
            total_amount = 0.0
            for _ in range(n_items):
                order_item_id += 1
                product_id = rng.randint(1, n_products)
                quantity = rng.randint(1, 3)
                unit_price = round(rng.uniform(4.99, 899.99), 2)
                total_amount += quantity * unit_price
                iw.writerow([order_item_id, order_id, product_id, quantity, unit_price])

            total_amount = round(total_amount, 2)
            ow.writerow([order_id, customer_id, order_date.isoformat(sep=" "),
                         status, total_amount])

            # not every order has a settled payment (pending/cancelled orders may not)
            if status in ("paid", "shipped", "refunded"):
                payment_id += 1
                pay_status = np_rng.choice(PAYMENT_STATUSES, p=PAYMENT_STATUS_WEIGHTS)
                method = np_rng.choice(PAYMENT_METHODS)
                payment_date = order_date + timedelta(minutes=rng.randint(1, 120))
                pw.writerow([payment_id, order_id, payment_date.isoformat(sep=" "),
                             total_amount, method, pay_status])


def generate_events(n_events, n_customers, out_dir, rng, np_rng):
    start, end = daterange_start_end()
    path = os.path.join(out_dir, "events.csv")

    customer_weights = zipf_weights(n_customers, np_rng)  # power users generate far more events

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["event_id", "customer_id", "event_type", "event_time", "metadata"])

        remaining = n_events
        event_id = 0
        with tqdm(total=n_events, desc="events.csv") as pbar:
            while remaining > 0:
                batch = min(CHUNK_SIZE, remaining)
                customer_ids = np_rng.choice(
                    np.arange(1, n_customers + 1), size=batch, p=customer_weights
                )
                event_types = np_rng.choice(EVENT_TYPES, size=batch, p=EVENT_TYPE_WEIGHTS)

                for i in range(batch):
                    event_id += 1
                    event_time = random_datetime_between(start, end, rng)
                    metadata = json.dumps({
                        "session_id": rng.randint(1, 10_000_000),
                        "device": rng.choice(["mobile", "desktop", "tablet"]),
                    })
                    writer.writerow([
                        event_id,
                        int(customer_ids[i]),
                        event_types[i],
                        event_time.isoformat(sep=" "),
                        metadata,
                    ])

                remaining -= batch
                pbar.update(batch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=SCALE_PRESETS.keys(), default="small",
                         help="Dataset scale preset (default: small, for local dev/testing)")
    parser.add_argument("--out", default="../data", help="Output directory for CSVs")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    preset = SCALE_PRESETS[args.scale]
    fake = Faker()
    Faker.seed(args.seed)
    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)

    print(f"Generating '{args.scale}' scale dataset into {args.out}/")
    print(f"  customers={preset['customers']:,}  products={preset['products']:,}  "
          f"orders={preset['orders']:,}  events={preset['events']:,}")

    generate_customers(preset["customers"], args.out, fake, rng, np_rng)
    generate_products(preset["products"], args.out, fake, rng, np_rng)
    generate_orders_items_payments(preset["orders"], preset["customers"],
                                    preset["products"], args.out, rng, np_rng)
    generate_events(preset["events"], preset["customers"], args.out, rng, np_rng)

    print("Done. CSVs are ready for bulk loading — see seed/load/ scripts per engine.")


if __name__ == "__main__":
    main()
