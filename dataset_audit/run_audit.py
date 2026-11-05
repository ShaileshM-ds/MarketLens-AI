import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


DOWNLOADS = Path(r"C:\Users\Impana Dechamma\Downloads")
OUT = Path(r"C:\Users\Impana Dechamma\MarketLens-AI\dataset_audit")
EXPECTED = [
    "product_id", "product_name", "seller_id", "seller_name", "timestamp",
    "price", "original_price", "currency", "marketplace", "product_url",
    "availability", "shipping_cost", "delivery_information", "discount",
    "seller_rating", "product_rating", "category", "source", "source_type",
    "listing_id", "coupon_discount", "payment_offer", "urgency_message",
    "stock_quantity", "product_condition", "seller_location", "last_updated",
]
SUPPORTED_CURRENCIES = {"INR", "USD", "GBP", "EUR", "JPY", "CAD", "AUD"}
SCHEMA_HEADER = [
    "filename", "relevant_marketplace_dataset", "exclusion_reason",
    "header_count", "expected_column_count", "missing_columns",
    "additional_columns", "duplicate_headers", "extra_spaces_or_inconsistent_case",
    "column_order_correct", "schema_status", "notes",
]
QUALITY_HEADER = [
    "filename", "row_number", "check", "column", "value", "reason", "severity",
]
MISALIGN_HEADER = [
    "filename", "row_number", "affected_columns", "observed_values", "reason",
]
STATS_HEADER = [
    "record_type", "filename", "product_id", "product_name", "listings_count",
    "sellers_count", "marketplace_names", "categories", "total_rows",
    "unique_products", "unique_sellers", "unique_marketplaces", "unique_categories",
    "products_with_multiple_sellers", "products_with_one_seller",
    "duplicate_records", "products_with_multiple_listings",
    "listing_ids_unique", "product_id_consistency", "category_counts",
    "currency_counts", "read_status",
]
DUPLICATE_HEADER = [
    "filename", "row_number", "duplicate_of_row", "duplicate_type",
    "matching_columns", "notes",
]


def text(value):
    return "" if value is None else str(value).strip()


def add_quality(rows, filename, row, check, column="", value="", reason="", severity="WARNING"):
    rows.append([filename, row, check, column, value, reason, severity])


def parse_number(value):
    value = text(value).replace(",", "").replace("$", "").replace("£", "")
    value = value.replace("€", "").replace("¥", "").replace("₹", "")
    if value.endswith("%"):
        value = value[:-1].strip()
    if not value:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def parse_timestamp(value):
    value = text(value)
    if not value:
        return False, "missing timestamp"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return False, "timestamp has no timezone offset; normalizer requires timezone-aware timestamps"
        return True, ""
    except ValueError:
        return False, "not a recognized ISO-8601 timestamp"


def get(row, *names):
    for name in names:
        value = row.get(name, "")
        if text(value):
            return text(value)
    return ""


def canonical_category(row):
    value = get(row, "category", "categories", "category_tree", "root_bs_category", "bs_category")
    if not value:
        return ""
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and parsed:
            leaf = parsed[-1]
            if isinstance(leaf, dict):
                return text(leaf.get("name")) or text(leaf.get("title")) or value
            return text(leaf)
    except (json.JSONDecodeError, TypeError):
        pass
    return value


def product_identity(row):
    return get(row, "product_id", "asin", "external_id", "input_asin")


def additional_seller_offers(value):
    if not text(value):
        return []
    try:
        offers = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return offers if isinstance(offers, list) else []


def seller_identity(row):
    return get(row, "seller_id", "seller_name", "buybox_seller")


def marketplace_identity(row):
    value = get(row, "marketplace", "domain")
    if "://" in value:
        value = urlsplit(value).hostname or value
    value = value.removeprefix("www.").rstrip("/")
    return value


def is_marketplace_product_header(header):
    names = {text(name).lower() for name in header}
    has_product = bool({"product_id", "asin", "external_id"} & names)
    has_seller = bool({"seller_id", "seller_name", "buybox_seller"} & names)
    has_price = bool({"price", "final_price"} & names)
    has_marketplace = bool({"marketplace", "domain", "product_url", "url"} & names)
    return has_product and has_seller and has_price and has_marketplace


def excluded_reason(header):
    names = {text(name).lower() for name in header}
    if names and names <= {"url"}:
        return "Marketplace URL input list, not product listing records"
    if "keyword" in names:
        return "Marketplace keyword/input list, not product listing records"
    if "dataset_id" in names and "status" in names:
        return "Scraper/API execution log, not product listing records"
    return "Header does not identify product records with product, seller, price, and marketplace fields"


def main():
    if not DOWNLOADS.is_dir():
        raise FileNotFoundError(f"Downloads folder not found: {DOWNLOADS}")
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(DOWNLOADS.rglob("*.csv"), key=lambda p: str(p).casefold())
    schema_rows = []
    quality_rows = []
    misalignment_rows = []
    stats_rows = []
    duplicate_rows = []
    datasets = []
    excluded = []
    read_errors = []
    products_across = defaultdict(lambda: {"files": set(), "names": set(), "categories": set()})

    for path in paths:
        relative = str(path.relative_to(DOWNLOADS))
        try:
            stream = path.open("r", encoding="utf-8-sig", newline="")
            reader = csv.reader(stream)
            header = next(reader, [])
        except Exception as exc:
            schema_rows.append([relative, "no", "Could not read header", 0, len(EXPECTED), "", "", "", "", "no", "read_error", f"{type(exc).__name__}: {exc}"])
            read_errors.append((relative, f"{type(exc).__name__}: {exc}"))
            continue

        relevant = is_marketplace_product_header(header)
        reason = "" if relevant else excluded_reason(header)
        normalized_header = [text(name) for name in header]
        duplicates = [name for name, count in Counter(normalized_header).items() if name and count > 1]
        odd_names = [
            f"{raw!r}" for raw in header
            if raw != text(raw) or text(raw) != text(raw).lower()
        ]
        if relevant and set(EXPECTED).issubset(normalized_header):
            missing = []
            additional = [name for name in normalized_header if name not in EXPECTED]
            ordered = normalized_header == EXPECTED
            schema_status = "PASS" if ordered and not duplicates and not odd_names else "FAIL"
        elif relevant:
            missing = [name for name in EXPECTED if name not in normalized_header]
            additional = [name for name in normalized_header if name not in EXPECTED]
            ordered = False
            schema_status = "FAIL"
        else:
            missing, additional, ordered, schema_status = [], [], "N/A", "EXCLUDED"
        notes = "Marketplace product records included in row audit" if relevant else reason
        schema_rows.append([
            relative, "yes" if relevant else "no", reason, len(header), len(EXPECTED),
            "; ".join(missing), "; ".join(additional), "; ".join(duplicates),
            "; ".join(odd_names), ordered, schema_status, notes,
        ])
        if not relevant:
            excluded.append((relative, reason))
            stream.close()
            continue

        d = {
            "filename": relative, "total": 0, "products": set(), "sellers": set(),
            "marketplaces": set(), "categories": Counter(), "product_sellers": defaultdict(set),
            "product_listings": Counter(), "currencies": Counter(), "full_hashes": {},
            "listing_ids": {}, "product_names": defaultdict(set), "product_categories": defaultdict(set),
            "product_marketplaces": defaultdict(set),
            "duplicate_count": 0, "row_errors": [], "encoding": "utf-8-sig",
        }
        try:
            for record_number, values in enumerate(reader, start=2):
                d["total"] += 1
                if len(values) != len(header):
                    add_quality(
                        quality_rows, relative, record_number, "row_column_count_mismatch",
                        value=len(values),
                        reason=f"Header has {len(header)} columns; record has {len(values)}",
                        severity="ERROR",
                    )
                row = {}
                for index, name in enumerate(normalized_header):
                    if name and name not in row:
                        row[name] = values[index] if index < len(values) else ""
                pid = product_identity(row)
                seller = seller_identity(row)
                market = marketplace_identity(row)
                category = canonical_category(row)
                name = get(row, "product_name", "title", "title_clean")
                listing = get(row, "listing_id")
                price_raw = get(row, "price", "final_price")
                original_raw = get(row, "original_price", "initial_price")
                shipping_raw = get(row, "shipping_cost", "shipping")
                currency = get(row, "currency").upper()
                timestamp_value = get(row, "timestamp", "observed_at")

                if pid:
                    d["products"].add(pid)
                    d["product_listings"][pid] += 1
                    if name:
                        d["product_names"][pid].add(name)
                    if category:
                        d["product_categories"][pid].add(category)
                    if market:
                        d["product_marketplaces"][pid].add(market)
                    aggregate = products_across[pid]
                    aggregate["files"].add(relative)
                    if name:
                        aggregate["names"].add(name)
                    if category:
                        aggregate["categories"].add(category)
                else:
                    add_quality(quality_rows, relative, record_number, "missing_product_id", "product_id/asin", "", "No product identifier", "ERROR")
                if not name:
                    add_quality(quality_rows, relative, record_number, "missing_product_name", "product_name/title", "", "No product name", "ERROR")
                if not get(row, "seller_id"):
                    add_quality(quality_rows, relative, record_number, "missing_seller_id", "seller_id", "", "Seller identifier is absent", "WARNING")
                if not get(row, "seller_name"):
                    add_quality(quality_rows, relative, record_number, "missing_seller_name", "seller_name", "", "Seller name is absent", "WARNING")
                if seller:
                    d["sellers"].add(seller)
                    if pid:
                        d["product_sellers"][pid].add(seller)
                alternate_offers = additional_seller_offers(row.get("other_sellers_prices", ""))
                for offer in alternate_offers:
                    if not isinstance(offer, dict):
                        continue
                    alternate_seller = get(offer, "seller_id", "seller_name", "seller_url")
                    if not alternate_seller or not pid:
                        continue
                    d["sellers"].add(alternate_seller)
                    d["product_sellers"][pid].add(alternate_seller)
                    d["product_listings"][pid] += 1
                if market:
                    d["marketplaces"].add(market)
                else:
                    add_quality(quality_rows, relative, record_number, "missing_marketplace", "marketplace/domain", "", "Marketplace identity is absent", "ERROR")
                if category:
                    d["categories"][category] += 1

                if not timestamp_value:
                    add_quality(quality_rows, relative, record_number, "missing_timestamp", "timestamp", "", "Observation timestamp is absent", "WARNING")
                else:
                    valid, why = parse_timestamp(timestamp_value)
                    if not valid:
                        add_quality(quality_rows, relative, record_number, "invalid_timestamp", "timestamp", timestamp_value, why, "ERROR")
                for timestamp_column in ("last_updated",):
                    if timestamp_column in row and text(row[timestamp_column]):
                        valid, why = parse_timestamp(row[timestamp_column])
                        if not valid:
                            add_quality(quality_rows, relative, record_number, "invalid_timestamp", timestamp_column, row[timestamp_column], why, "ERROR")

                if not price_raw:
                    add_quality(quality_rows, relative, record_number, "missing_price", "price/final_price", "", "Selling price is absent", "ERROR")
                else:
                    price = parse_number(price_raw)
                    if price is None or price <= 0:
                        add_quality(quality_rows, relative, record_number, "invalid_price", "price/final_price", price_raw, "Price must be a positive finite number", "ERROR")
                original = parse_number(original_raw) if original_raw else None
                price = parse_number(price_raw)
                if original_raw and original is None:
                    add_quality(quality_rows, relative, record_number, "invalid_original_price", "original_price/initial_price", original_raw, "Original price is not numeric", "ERROR")
                elif original is not None and price is not None and original < price:
                    add_quality(quality_rows, relative, record_number, "original_price_below_selling_price", "original_price/initial_price", original_raw, f"Original price {original_raw} is lower than selling price {price_raw}", "WARNING")

                if currency:
                    d["currencies"][currency] += 1
                    if currency not in SUPPORTED_CURRENCIES:
                        add_quality(quality_rows, relative, record_number, "unsupported_currency", "currency", currency, "Currency is outside the normalizer's supported set", "WARNING")
                else:
                    add_quality(quality_rows, relative, record_number, "missing_currency", "currency", "", "Currency is absent", "ERROR")
                if shipping_raw:
                    shipping = parse_number(shipping_raw)
                    if shipping is None or shipping < 0:
                        add_quality(quality_rows, relative, record_number, "invalid_shipping_cost", "shipping_cost/shipping", shipping_raw, "Shipping cost must be numeric and nonnegative", "ERROR")

                discount = get(row, "discount")
                if discount:
                    discount_num = parse_number(discount)
                    if discount_num is None or not 0 <= discount_num <= 100:
                        add_quality(quality_rows, relative, record_number, "invalid_discount", "discount", discount, "Discount must be a percentage from 0 to 100", "WARNING")
                for rating_column in ("seller_rating", "product_rating", "rating", "buybox_seller_rating"):
                    value = get(row, rating_column)
                    if value:
                        rating = parse_number(value)
                        if rating is None or not 0 <= rating <= 5:
                            add_quality(quality_rows, relative, record_number, "suspicious_rating", rating_column, value, "Rating is not numeric in the expected 0-5 range", "WARNING")
                quantity = get(row, "stock_quantity", "max_quantity_available", "number_of_sellers")
                if quantity:
                    parsed_quantity = parse_number(quantity)
                    if parsed_quantity is None or parsed_quantity < 0 or not parsed_quantity.is_integer():
                        add_quality(quality_rows, relative, record_number, "invalid_stock_quantity", "stock_quantity/max_quantity_available", quantity, "Quantity should be a nonnegative integer", "WARNING")

                if "listing_id" in row:
                    if not listing:
                        add_quality(quality_rows, relative, record_number, "missing_listing_id", "listing_id", "", "Listing ID is absent", "WARNING")
                    elif listing in d["listing_ids"]:
                        first_row, first_pid, first_seller = d["listing_ids"][listing]
                        kind = "listing_id_collision" if (pid != first_pid or seller != first_seller) else "repeated_listing_id"
                        duplicate_rows.append([relative, record_number, first_row, kind, "listing_id", f"listing_id={listing}"])
                        if kind == "listing_id_collision":
                            add_quality(quality_rows, relative, record_number, "listing_id_not_unique", "listing_id", listing, f"Already used by row {first_row} for another product/seller", "ERROR")
                    else:
                        d["listing_ids"][listing] = (record_number, pid, seller)

                if "product_url" in row:
                    availability = get(row, "availability")
                    product_url = get(row, "product_url")
                    if availability and re.search(r"https?://|www\.", availability, re.I):
                        misalignment_rows.append([relative, record_number, "availability; product_url", f"availability={availability!r}; product_url={product_url!r}", "URL-shaped value appears in availability, which should describe stock status"])
                    if product_url and not re.search(r"https?://|www\.", product_url, re.I) and re.search(r"\b(in stock|out of stock|refurbished|used|new|synthetic)\b", product_url, re.I):
                        misalignment_rows.append([relative, record_number, "product_url; availability/product_condition", f"product_url={product_url!r}", "Status/condition-like value appears in product_url"])
                    for column in ("product_rating",):
                        value = get(row, column)
                        if value and parse_number(value) is None and category and value.casefold() == category.casefold():
                            misalignment_rows.append([relative, record_number, column, f"{column}={value!r}; category={category!r}", "Category-like text appears in numeric product_rating"])
                    stock = get(row, "stock_quantity")
                    condition = get(row, "product_condition")
                    if stock and parse_number(stock) is None and re.search(r"\b(refurbished|used|new|open.box)\b", stock, re.I):
                        misalignment_rows.append([relative, record_number, "stock_quantity; product_condition", f"stock_quantity={stock!r}; product_condition={condition!r}", "Condition-like text appears in numeric stock_quantity"])
                    source = get(row, "source")
                    source_type = get(row, "source_type")
                    source_types = {"synthetic", "generated", "scraped", "imported", "manual", "marketplace", "live", "demo", "api"}
                    if source_type and source_type.casefold() not in source_types and source.casefold() in source_types:
                        misalignment_rows.append([relative, record_number, "source; source_type", f"source={source!r}; source_type={source_type!r}", "source_type-like token appears in source while source_type contains an unrecognized descriptive value"])
                    if source_type and source_type.casefold() not in source_types | {"generated demonstration data"}:
                        add_quality(quality_rows, relative, record_number, "unrecognized_source_type", "source_type", source_type, "Value does not match common source-type labels; confirm allowed project vocabulary", "WARNING")

                row_digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                if row_digest in d["full_hashes"]:
                    first_row = d["full_hashes"][row_digest]
                    d["duplicate_count"] += 1
                    duplicate_rows.append([relative, record_number, first_row, "exact_duplicate_record", "all columns", "All cell values exactly match"])
                else:
                    d["full_hashes"][row_digest] = record_number
        except Exception as exc:
            d["row_errors"].append(f"{type(exc).__name__}: {exc}")
            read_errors.append((relative, d["row_errors"][-1]))
            add_quality(quality_rows, relative, "", "file_read_error", "", "", d["row_errors"][-1], "ERROR")
        finally:
            stream.close()

        if relevant and "listing_id" not in normalized_header:
            add_quality(
                quality_rows, relative, "", "listing_id_column_missing", "listing_id", "",
                "Source schema has no listing_id; assign or derive only after explicit review",
                "WARNING",
            )
        multi = sum(1 for sellers in d["product_sellers"].values() if len(sellers) > 1)
        single = sum(1 for sellers in d["product_sellers"].values() if len(sellers) == 1)
        multi_listings = sum(1 for count in d["product_listings"].values() if count > 1)
        consistency = []
        for pid, names in d["product_names"].items():
            if len(names) > 1:
                consistency.append(f"{pid}: conflicting names")
                add_quality(quality_rows, relative, "", "product_id_conflicting_names", "product_id", pid, "Same product ID has multiple product names: " + " | ".join(sorted(names)), "WARNING")
        for pid, categories in d["product_categories"].items():
            if len(categories) > 1:
                consistency.append(f"{pid}: conflicting categories")
                add_quality(quality_rows, relative, "", "product_id_conflicting_categories", "product_id", pid, "Same product ID has multiple categories: " + " | ".join(sorted(categories)), "WARNING")
        listing_ids_unique = "yes" if "listing_id" in normalized_header and not any(
                row[0] == relative and row[3] == "listing_id_collision" for row in duplicate_rows
            ) else "no"
        category_counts = json.dumps(dict(sorted(d["categories"].items())), ensure_ascii=False)
        currency_counts = json.dumps(dict(sorted(d["currencies"].items())), ensure_ascii=False)
        consistency_summary = "; ".join(consistency) if consistency else "no conflicting names/categories detected"
        for pid in sorted(d["products"]):
            names = sorted(d["product_names"].get(pid, set()))
            cats = sorted(d["product_categories"].get(pid, set()))
            stats_rows.append([
                "product", relative, pid, " | ".join(names),
                d["product_listings"][pid], len(d["product_sellers"].get(pid, set())),
                "; ".join(sorted(d["product_marketplaces"].get(pid, set()))), "; ".join(cats),
                "", "", "", "", "", "", "", "", "", "", "",
                "", "", "ERROR" if d["row_errors"] else "OK",
            ])
        stats_rows.append([
            "dataset_summary", relative, "", "", "", "",
            "; ".join(sorted(d["marketplaces"])), "",
            d["total"], len(d["products"]), len(d["sellers"]),
            len(d["marketplaces"]), len(d["categories"]), multi, single,
            d["duplicate_count"], multi_listings, listing_ids_unique,
            consistency_summary, category_counts, currency_counts,
            "ERROR" if d["row_errors"] else "OK",
        ])
        datasets.append(d)

    cross_overlaps = {
        pid: values for pid, values in products_across.items()
        if len(values["files"]) > 1
    }
    conflicting = {
        pid: values for pid, values in cross_overlaps.items()
        if len(values["names"]) > 1 or len(values["categories"]) > 1
    }
    all_categories = Counter()
    all_currencies = Counter()
    for d in datasets:
        all_categories.update(d["categories"])
        all_currencies.update(d["currencies"])

    with (OUT / "file_schema_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f); writer.writerow(SCHEMA_HEADER); writer.writerows(schema_rows)
    with (OUT / "data_quality_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f); writer.writerow(QUALITY_HEADER); writer.writerows(quality_rows)
    with (OUT / "misalignment_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f); writer.writerow(MISALIGN_HEADER); writer.writerows(misalignment_rows)
    with (OUT / "product_seller_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f); writer.writerow(STATS_HEADER); writer.writerows(stats_rows)
    with (OUT / "duplicate_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f); writer.writerow(DUPLICATE_HEADER); writer.writerows(duplicate_rows)

    relevant_names = [d["filename"] for d in datasets]
    failed_schema = [r[0] for r in schema_rows if r[1] == "yes" and r[10] != "PASS"]
    lines = [
        "MarketLens-AI Downloads dataset audit",
        "Audit date: 2026-11-04",
        f"Search root: {DOWNLOADS}",
        "",
        f"CSV files found recursively: {len(paths)}",
        f"Relevant marketplace product datasets: {len(datasets)}",
        f"Excluded unrelated/input/log CSV files: {len(excluded)}",
        f"Read errors: {len(read_errors)}",
        "",
        "Relevant marketplace datasets:",
    ]
    lines.extend(f"- {name}" for name in relevant_names)
    lines.extend(["", "Excluded CSV files (header-inspected, not treated as marketplace listings):"])
    lines.extend(f"- {name}: {reason}" for name, reason in excluded)
    lines.extend(["", "Read errors:"])
    lines.extend(f"- {name}: {error}" for name, error in read_errors)
    if not read_errors:
        lines.append("- None")
    lines.extend([
        "",
        f"Expected 27-column schema consistent across relevant files: {'yes' if len(datasets) > 0 and not failed_schema else 'no'}",
        f"Relevant files failing exact 27-column schema: {len(failed_schema)}",
    ])
    lines.extend(f"- {name}" for name in failed_schema)
    lines.extend([
        "",
        "Observed categories (derived from category/category_tree/categories):",
        json.dumps(dict(sorted(all_categories.items())), ensure_ascii=False, indent=2),
        "",
        "Observed currencies:",
        json.dumps(dict(sorted(all_currencies.items())), ensure_ascii=False, indent=2),
        "",
        f"Suspiciously misaligned rows: {len(misalignment_rows)}",
        f"Duplicate record rows: {len(duplicate_rows)}",
        f"Product IDs overlapping across files: {len(cross_overlaps)}",
        f"Overlapping IDs with conflicting names or categories: {len(conflicting)}",
        "",
        "Combination assessment:",
        "The files cannot be safely concatenated as-is if their schema report does not show the same exact 27-column header and order. The marketplace exports include provider-specific columns; map them explicitly into the 27-column schema, preserving category, currency, marketplace, seller, provenance, timestamps, and listing identity, then rerun the audit. Product IDs shared across files must be checked for matching names/categories before union.",
        "",
        "Cross-file product ID overlaps:",
    ])
    if cross_overlaps:
        for pid, values in sorted(cross_overlaps.items()):
            lines.append(f"- {pid}: files={'; '.join(sorted(values['files']))}; names={'; '.join(sorted(values['names']))}; categories={'; '.join(sorted(values['categories']))}")
    else:
        lines.append("- None detected")
    lines.extend(["", "Cross-file product ID conflicts:"])
    if conflicting:
        for pid, values in sorted(conflicting.items()):
            lines.append(f"- {pid}: names={'; '.join(sorted(values['names']))}; categories={'; '.join(sorted(values['categories']))}; files={'; '.join(sorted(values['files']))}")
    else:
        lines.append("- None detected")
    lines.extend([
        "",
        "Normalizer and INR:",
        "The current normalizer already accepts INR and converts INR major units to minor units using a 100x multiplier. It does not natively map the proposed 27-column schema; explicit field mapping is needed, and product/listing identity and source provenance must be reviewed. It also requires a supported currency, positive price, marketplace and external product ID, seller identity, and timezone-aware observation timestamp.",
        "",
        "Historical generator and INR:",
        "The current history generator is explicitly USD-only: anchor extraction skips non-USD observations, and generated-price conversion is currency-aware but does not make INR anchors eligible. INR historical generation therefore requires code changes and dedicated currency/rounding tests. Do not generate synthetic history as a substitute for observed source history.",
        "",
        "Before import:",
        "1. Resolve exact-schema failures and every row in misalignment_report.csv; do not shift cells automatically.",
        "2. Explicitly map provider columns (title/asin/domain/final_price/initial_price/rating/category tree/seller fields) into the target schema.",
        "3. Resolve missing seller and listing identifiers, timestamps/timezones, invalid prices/discounts/shipping/ratings, and product-ID conflicts.",
        "4. Confirm currency policy (including INR) and category labels, then rerun this read-only audit and the existing import dry-run.",
        "5. Import only after review; keep generated history separate and clearly identified.",
        "",
        "Row numbers in reports are CSV record numbers, with the header as row 1. Excluded files were only header-inspected to avoid reading unrelated datasets.",
    ])
    (OUT / "overall_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    cross_lines = [
        "Cross-dataset compatibility report",
        "Audit date: 2026-11-04",
        f"Marketplace datasets compared: {len(datasets)}",
        f"Product IDs overlapping between files: {len(cross_overlaps)}",
        f"Overlapping IDs with conflicting product names/categories: {len(conflicting)}",
        "",
        "Overlapping product IDs:",
    ]
    for pid, values in sorted(cross_overlaps.items()):
        cross_lines.append(f"- {pid}: files={'; '.join(sorted(values['files']))}; names={'; '.join(sorted(values['names']))}; categories={'; '.join(sorted(values['categories']))}")
    if not cross_overlaps:
        cross_lines.append("- None detected")
    cross_lines.extend(["", "Conflicts:"])
    for pid, values in sorted(conflicting.items()):
        cross_lines.append(f"- {pid}: names={'; '.join(sorted(values['names']))}; categories={'; '.join(sorted(values['categories']))}; files={'; '.join(sorted(values['files']))}")
    if not conflicting:
        cross_lines.append("- None detected")
    cross_lines.extend([
        "",
        "Schema compatibility",
        f"Exact 27-column schema failures among relevant datasets: {len(failed_schema)}",
        "See file_schema_report.csv for per-file headers. Files with different source schemas cannot be concatenated directly; normalize through an explicit, reviewed mapping first.",
        "",
        "Category consistency",
        json.dumps(dict(sorted(all_categories.items())), ensure_ascii=False, indent=2),
        "",
        "Direct unified-dataset combination: not safe until the schemas are normalized and every product-ID/name/category and listing-ID collision is resolved.",
    ])
    (OUT / "cross_dataset_report.txt").write_text("\n".join(cross_lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "csv_found": len(paths), "datasets": len(datasets), "excluded": len(excluded),
        "schema_failures": len(failed_schema), "misalignments": len(misalignment_rows),
        "quality_findings": len(quality_rows), "duplicates": len(duplicate_rows),
        "overlaps": len(cross_overlaps), "conflicts": len(conflicting),
        "read_errors": read_errors,
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
