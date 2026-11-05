"""Opt-in adapters for the audited marketplace CSV source files.

This module is deliberately separate from the application's existing import
route and normalizer. It has no database dependency and never writes data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CANONICAL_COLUMNS = (
    "product_id",
    "product_name",
    "seller_id",
    "seller_name",
    "timestamp",
    "price",
    "original_price",
    "currency",
    "marketplace",
    "product_url",
    "availability",
    "shipping_cost",
    "delivery_information",
    "discount",
    "seller_rating",
    "product_rating",
    "category",
    "source",
    "source_type",
    "listing_id",
    "coupon_discount",
    "payment_offer",
    "urgency_message",
    "stock_quantity",
    "product_condition",
    "seller_location",
    "last_updated",
)

AMAZON_SOURCE_FILES = frozenset({
    "amazon_products_32.csv",
    "charger_final_data.csv",
    "charger_snapshot.csv",
    "coolingPad_captureCard_final.csv",
    "coolingPad_captureCard_snapshot.csv",
    "multiple_products_final.csv",
    "multiple_products_snapshot.csv",
    "sd_mu9exytq1cwty0ftme.csv",
    "usb_keyboard_mouse_final.csv",
    "usb_keyboard_mouse_snapshot.csv",
})
CANONICAL_SOURCE_FILE = "mens_clothing_3_platform_dataset-1.csv"
ENRICHED_SOURCE_FILES = frozenset({
    "cameras_dataset_enriched.csv",
    "mens_clothing_dataset_enriched.csv",
    "smart_home_dataset_enriched.csv",
    "wearables_dataset_enriched.csv",
    "womens_clothing_dataset_enriched.csv",
})
ENRICHMENT_COLUMNS = ("image_url", "review", "rating", "review_source")
ENRICHED_SOURCE_TIMEZONE = "Asia/Kolkata"
SUPPORTED_CURRENCIES = frozenset({
    "INR", "USD", "GBP", "EUR", "JPY", "CAD", "AUD",
})
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


class NestedOfferPolicy(str, Enum):
    """Controls how ``other_sellers_prices`` is handled."""

    PRESERVE_ONLY = "preserve_only"
    EXPAND = "expand"
    REJECT_IF_PRESENT = "reject_if_present"


@dataclass(frozen=True)
class QuarantinedRecord:
    source_filename: str
    source_row_number: int
    reason: str
    source_record: Mapping[str, Any] = field(repr=False)
    source_offer_index: int | None = None


@dataclass(frozen=True)
class AdaptedRecord:
    """Canonical listing observation plus identities, provenance, and extensions.

    Enriched-source image and review fields remain in ``enrichment`` rather
    than expanding the 27-column canonical mapping.
    """

    canonical: Mapping[str, str | None]
    product_identity: tuple[str, str]
    listing_identity: tuple[str, str, str, str]
    observation_identity: str
    provenance: Mapping[str, Any]
    source_record: Mapping[str, Any] = field(repr=False)
    enrichment: Mapping[str, str | None] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdaptationResult:
    records: tuple[AdaptedRecord, ...]
    quarantined: tuple[QuarantinedRecord, ...]


class AdapterContext:
    """Optional state for detecting identity conflicts across files."""

    def __init__(self) -> None:
        self._products: dict[tuple[str, str], tuple[str, str]] = {}
        self._source_listing_ids: dict[str, tuple[str, str, str, str]] = {}
        self._observations: dict[tuple[str, ...], str] = {}

    def register_product(
        self,
        identity: tuple[str, str],
        product_name: str,
        category: str,
    ) -> str | None:
        observed = (
            _identity_text(product_name),
            _identity_text(category),
        )
        previous = self._products.get(identity)
        if previous is None:
            self._products[identity] = observed
            return None
        conflicts = []
        if previous[0] and observed[0] and previous[0] != observed[0]:
            conflicts.append("product_name")
        if previous[1] and observed[1] and previous[1] != observed[1]:
            conflicts.append("category")
        if conflicts:
            return "unresolved_product_identity_conflict:" + ",".join(conflicts)
        return None

    def register_source_listing_id(
        self,
        listing_id: str,
        identity: tuple[str, str, str, str],
    ) -> str | None:
        previous = self._source_listing_ids.get(listing_id)
        if previous is None:
            self._source_listing_ids[listing_id] = identity
            return None
        if previous != identity:
            return "source_listing_id_collision"
        return None

    def register_observation(
        self,
        identity: tuple[str, ...],
        fingerprint: str,
    ) -> str | None:
        previous = self._observations.get(identity)
        if previous is None:
            self._observations[identity] = fingerprint
            return None
        if previous == fingerprint:
            return "duplicate_observation"
        return "conflicting_observation_identity"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _preserved_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _identity_text(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _as_decimal(value: Any) -> Decimal | None:
    text = _text(value).replace(",", "")
    if not text:
        return None
    if not _DECIMAL_RE.fullmatch(text):
        raise ValueError(f"invalid numeric value: {value!r}")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"numeric value must be finite: {value!r}")
    return number


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _timestamp(
    value: Any,
    naive_timezone: str | tzinfo | None,
) -> str:
    raw = _text(value)
    if not raw:
        raise ValueError("missing observation timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 observation timestamp") from exc
    if parsed.tzinfo is None:
        if naive_timezone is None:
            raise ValueError("naive timestamp requires an explicitly supplied timezone")
        try:
            zone = (
                ZoneInfo(naive_timezone)
                if isinstance(naive_timezone, str)
                else naive_timezone
            )
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {naive_timezone}") from exc
        if zone is None:
            raise ValueError("invalid timezone")
        if isinstance(zone, ZoneInfo):
            candidates = [
                parsed.replace(tzinfo=zone, fold=fold)
                for fold in (0, 1)
            ]
            valid = [
                candidate for candidate in candidates
                if candidate.astimezone(timezone.utc)
                .astimezone(zone)
                .replace(tzinfo=None) == parsed
            ]
            if not valid:
                raise ValueError("naive timestamp falls in a nonexistent local-time interval")
            if (
                len(valid) == 2
                and valid[0].utcoffset() != valid[1].utcoffset()
            ):
                raise ValueError("ambiguous local timestamp requires an explicit offset")
            parsed = valid[0]
        else:
            parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc).isoformat()


def _json_value(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label}") from exc


def _delivery_text(value: Any) -> str | None:
    parsed = _json_value(value, "delivery")
    if parsed is None:
        return None
    if isinstance(parsed, list):
        pieces = [_text(item) for item in parsed if _text(item)]
        return "; ".join(pieces) or None
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    return _text(parsed) or None


def _category(row: Mapping[str, Any]) -> str | None:
    direct = _text(row.get("category"))
    if direct:
        return direct
    for key in ("categories", "category_tree"):
        parsed = _json_value(row.get(key), key)
        if isinstance(parsed, list) and parsed:
            leaf = parsed[-1]
            if isinstance(leaf, Mapping):
                result = _text(leaf.get("name") or leaf.get("title"))
            else:
                result = _text(leaf)
            if result:
                return result
        elif isinstance(parsed, str) and parsed:
            return parsed
    return _text(row.get("bs_category") or row.get("root_bs_category")) or None


def _seller_id(value: Any, seller_url: Any = None) -> str | None:
    explicit = _text(value)
    if explicit:
        return explicit
    url = _text(seller_url)
    if not url:
        return None
    return _text(parse_qs(urlsplit(url).query).get("seller", [""])[0]) or None


def _marketplace(row: Mapping[str, Any], amazon: bool) -> str | None:
    direct = _text(row.get("marketplace"))
    if direct:
        return direct
    value = _text(row.get("domain"))
    if not value:
        value = _text(row.get("product_url") or row.get("url") or row.get("input_url"))
    if not value:
        return None
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    if amazon and host.startswith("amazon."):
        return "amazon.com"
    return host


def _product_id(row: Mapping[str, Any], amazon: bool) -> str | None:
    if not amazon:
        return _text(row.get("product_id")) or None
    product_id = _text(row.get("asin")) or _text(row.get("input_asin"))
    if not product_id:
        return None
    return product_id.upper()


def _source_schema(filename: str) -> str:
    name = Path(filename).name
    if name == CANONICAL_SOURCE_FILE:
        return "canonical"
    if name in ENRICHED_SOURCE_FILES:
        return "enriched"
    if name in AMAZON_SOURCE_FILES:
        return "amazon"
    raise ValueError(f"unsupported marketplace dataset: {filename}")


def validate_source_headers(filename: str, headers: Iterable[str]) -> tuple[str, ...]:
    """Return header issues for one of the eleven audited source files."""
    schema = _source_schema(filename)
    header_list = list(headers)
    normalized = [str(column).strip() for column in header_list]
    issues: list[str] = []
    if any(not column for column in normalized):
        issues.append("blank_header")
    if len(set(normalized)) != len(normalized):
        issues.append("duplicate_headers")
    if schema == "canonical":
        if tuple(normalized) != CANONICAL_COLUMNS:
            issues.append("canonical_header_must_match_exact_order")
    elif schema == "enriched":
        if tuple(normalized) != (*CANONICAL_COLUMNS, *ENRICHMENT_COLUMNS):
            issues.append("enriched_header_must_match_canonical_and_extension_order")
    else:
        required = {"asin", "title", "seller_id", "timestamp", "currency", "final_price"}
        missing = sorted(required - set(normalized))
        if missing:
            issues.append("missing_provider_headers:" + ",".join(missing))
    return tuple(issues)


def _stable_id(identity: tuple[str, ...]) -> str:
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nested_offers(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = _json_value(row.get("other_sellers_prices"), "other_sellers_prices")
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("other_sellers_prices must be a JSON array")
    if any(not isinstance(offer, Mapping) for offer in value):
        raise ValueError("every nested seller offer must be an object")
    return value


def _optional_rating(
    row: Mapping[str, Any],
    key: str,
    warnings: list[str],
) -> str | None:
    value = _text(row.get(key))
    if not value:
        return None
    try:
        rating = _as_decimal(value)
    except ValueError:
        warnings.append(f"invalid_optional_{key}")
        return None
    if rating is None or rating < 0 or rating > 5:
        warnings.append(f"invalid_optional_{key}")
        return None
    return _decimal_text(rating)


def _canonical_row(
    source: Mapping[str, Any],
    *,
    schema: str,
    source_filename: str,
    source_row_number: int,
    naive_timezone: str | tzinfo | None,
    offer: Mapping[str, Any] | None = None,
) -> tuple[dict[str, str | None], tuple[str, ...], tuple[str, str]]:
    warnings: list[str] = []
    is_nested = offer is not None
    row = dict(source)
    if is_nested:
        row["seller_id"] = _seller_id(None, offer.get("seller_url"))
        row["seller_name"] = offer.get("seller_name")
        row["seller_url"] = offer.get("seller_url")
        row["final_price"] = offer.get("price")
        row["delivery"] = offer.get("delivery")
        row["variant_condition"] = offer.get("condition")
        row["seller_rating"] = offer.get("seller_rating")

    amazon = schema == "amazon"
    product_id = _product_id(row, amazon)
    marketplace = _marketplace(row, amazon)
    seller_id = _seller_id(row.get("seller_id"), row.get("seller_url"))
    if not product_id:
        raise ValueError("missing required product identity")
    if not marketplace:
        raise ValueError("missing required marketplace")
    if not seller_id:
        raise ValueError("missing required seller identity")

    if schema in ("canonical", "enriched"):
        product_name = _text(row.get("product_name")) or None
        currency = _text(row.get("currency")).upper()
        price_raw = row.get("price")
        original_raw = row.get("original_price")
        source_timestamp = row.get("timestamp")
        product_url = _text(row.get("product_url")) or None
        try:
            category = _category(row)
        except ValueError:
            category = None
            warnings.append("invalid_optional_category")
        condition = _text(row.get("product_condition")) or None
        source_value = _text(row.get("source")) or source_filename
        source_type = _text(row.get("source_type")) or None
        seller_name = _text(row.get("seller_name")) or None
        seller_rating = _optional_rating(row, "seller_rating", warnings)
        product_rating = _optional_rating(row, "product_rating", warnings)
        availability = _text(row.get("availability")) or None
        shipping_raw = row.get("shipping_cost")
        delivery = _text(row.get("delivery_information")) or None
        source_discount = row.get("discount")
        if schema == "enriched":
            coupon_discount = None
            if _preserved_text(row.get("coupon_discount")) is not None:
                warnings.append("coupon_discount_unit_unconfirmed")
        else:
            coupon_discount = _text(row.get("coupon_discount")) or None
        payment_offer = _text(row.get("payment_offer")) or None
        urgency = _text(row.get("urgency_message")) or None
        stock_raw = row.get("stock_quantity")
        last_updated_raw = row.get("last_updated")
        seller_location = _text(row.get("seller_location")) or None
        listing_id = _text(row.get("listing_id")) or None
        product_identity = ("canonical", product_id)
    else:
        product_name = _text(row.get("title") or row.get("title_clean")) or None
        currency = _text(row.get("currency")).upper()
        price_raw = row.get("final_price")
        if not _text(price_raw):
            buybox = _json_value(row.get("buybox_prices"), "buybox_prices")
            if isinstance(buybox, Mapping):
                price_raw = buybox.get("final_price")
        original_raw = row.get("initial_price")
        if not _text(original_raw):
            buybox = _json_value(row.get("buybox_prices"), "buybox_prices")
            if isinstance(buybox, Mapping):
                original_raw = buybox.get("initial_price")
        source_timestamp = row.get("timestamp")
        product_url = (
            _text(row.get("url") or row.get("input_url") or row.get("origin_url"))
            or None
        )
        try:
            category = _category(row)
        except ValueError:
            category = None
            warnings.append("invalid_optional_category")
        condition = _text(row.get("variant_condition")) or None
        if is_nested:
            condition = _text(offer.get("condition")) or condition
        source_value = source_filename
        source_type = "scraped"
        seller_name = _text(row.get("seller_name") or row.get("buybox_seller")) or None
        seller_rating_source = (
            row.get("seller_rating") if is_nested else row.get("buybox_seller_rating")
        )
        seller_rating = _optional_rating(
            {"seller_rating": seller_rating_source}, "seller_rating", warnings
        )
        product_rating = _optional_rating(row, "rating", warnings)
        availability = _text(row.get("availability")) or None
        shipping_raw = None
        try:
            delivery = _delivery_text(row.get("delivery"))
        except ValueError:
            delivery = None
            warnings.append("invalid_optional_delivery")
        coupon_discount = None
        payment_offer = _text(row.get("amazon_checkout_promotion")) or None
        urgency = None
        stock_raw = None
        last_updated_raw = None
        seller_location = None
        listing_id = None
        product_identity = (marketplace, product_id)

    if not product_name:
        raise ValueError("missing required product name")
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported or missing currency: {currency or '(blank)'}")
    observed_at = _timestamp(source_timestamp, naive_timezone)
    price = _as_decimal(price_raw)
    if price is None or price <= 0:
        raise ValueError("missing or nonpositive selling price")

    original_price: Decimal | None
    try:
        original_price = _as_decimal(original_raw)
        if original_price is not None and original_price < 0:
            warnings.append("invalid_optional_original_price")
            original_price = None
    except ValueError:
        warnings.append("invalid_optional_original_price")
        original_price = None

    discount: Decimal | None = None
    if original_price is not None:
        if original_price > price:
            discount = (
                (original_price - price) * Decimal(100) / original_price
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif original_price < price:
            warnings.append("original_price_below_selling_price")
        else:
            discount = Decimal("0.00")
    if schema in ("canonical", "enriched") and source_discount not in (None, ""):
        try:
            supplied_discount = _as_decimal(source_discount)
            if supplied_discount is not None and discount is not None:
                if abs(supplied_discount - discount) > Decimal("0.51"):
                    warnings.append("source_discount_disagrees_with_price_calculation")
        except ValueError:
            warnings.append("invalid_source_discount")
    elif schema == "amazon" and _text(row.get("discount")):
        try:
            raw_discount = _as_decimal(
                _text(row.get("discount")).removesuffix("%")
            )
            if raw_discount is not None and discount is not None:
                if abs(abs(raw_discount) - discount) > Decimal("1.01"):
                    warnings.append("provider_discount_disagrees_with_price_calculation")
        except ValueError:
            warnings.append("invalid_provider_discount")

    shipping: str | None = None
    try:
        shipping_amount = _as_decimal(shipping_raw)
        if shipping_amount is not None and shipping_amount < 0:
            warnings.append("invalid_optional_shipping_cost")
        else:
            shipping = _decimal_text(shipping_amount)
    except ValueError:
        warnings.append("invalid_optional_shipping_cost")

    stock: str | None = None
    if stock_raw not in (None, ""):
        try:
            stock_amount = _as_decimal(stock_raw)
            if (
                stock_amount is None
                or stock_amount < 0
                or stock_amount != stock_amount.to_integral_value()
            ):
                warnings.append("invalid_optional_stock_quantity")
            else:
                stock = str(int(stock_amount))
        except ValueError:
            warnings.append("invalid_optional_stock_quantity")

    last_updated = None
    if last_updated_raw not in (None, ""):
        try:
            last_updated = _timestamp(last_updated_raw, naive_timezone)
        except ValueError:
            warnings.append("invalid_optional_last_updated")

    normalized_condition = _identity_text(condition) or "unknown"
    stable_listing_identity = (
        marketplace,
        product_id,
        seller_id,
        normalized_condition,
    )
    if schema in ("canonical", "enriched") and listing_id:
        canonical_listing_id = listing_id
    else:
        canonical_listing_id = "lst_" + _stable_id(stable_listing_identity)

    canonical: dict[str, str | None] = {
        "product_id": product_id,
        "product_name": product_name,
        "seller_id": seller_id,
        "seller_name": seller_name,
        "timestamp": observed_at,
        "price": _decimal_text(price),
        "original_price": _decimal_text(original_price),
        "currency": currency,
        "marketplace": marketplace,
        "product_url": product_url,
        "availability": availability,
        "shipping_cost": shipping,
        "delivery_information": delivery,
        "discount": _decimal_text(discount),
        "seller_rating": seller_rating,
        "product_rating": product_rating,
        "category": category,
        "source": source_value,
        "source_type": source_type,
        "listing_id": canonical_listing_id,
        "coupon_discount": coupon_discount,
        "payment_offer": payment_offer,
        "urgency_message": urgency,
        "stock_quantity": stock,
        "product_condition": condition,
        "seller_location": seller_location,
        "last_updated": last_updated,
    }
    return canonical, tuple(dict.fromkeys(warnings)), product_identity


def adapt_records(
    rows: Iterable[Mapping[str, Any]],
    source_filename: str,
    *,
    first_source_row_number: int = 2,
    naive_timezone: str | tzinfo | None = None,
    nested_offer_policy: NestedOfferPolicy = NestedOfferPolicy.PRESERVE_ONLY,
    context: AdapterContext | None = None,
) -> AdaptationResult:
    """Adapt records without writing them or changing the input mappings.

    Required fields are product identity/name, seller identity, marketplace,
    supported currency, positive price, and a timezone-aware observation time.
    Seller name and other optional fields remain null when unavailable.

    Nested offers are preserved in ``source_record`` by default. EXPAND is
    explicit; repeated seller/condition identities are quarantined rather than
    merged or assigned price-dependent IDs.

    Enriched sources use the confirmed IST timezone. Their uncertain
    ``coupon_discount`` remains in raw provenance, and review-level ratings
    remain outside canonical ``product_rating``.
    """
    schema = _source_schema(source_filename)
    effective_naive_timezone = naive_timezone
    if schema == "enriched":
        if naive_timezone not in (None, ENRICHED_SOURCE_TIMEZONE):
            raise ValueError(
                f"enriched dataset timestamps are fixed to {ENRICHED_SOURCE_TIMEZONE}"
            )
        effective_naive_timezone = ENRICHED_SOURCE_TIMEZONE
    policy = NestedOfferPolicy(nested_offer_policy)
    state = context if context is not None else AdapterContext()
    records: list[AdaptedRecord] = []
    quarantined: list[QuarantinedRecord] = []

    for row_number, raw in enumerate(rows, start=first_source_row_number):
        source_record = dict(raw) if isinstance(raw, Mapping) else {"_value": raw}
        if not isinstance(raw, Mapping):
            quarantined.append(QuarantinedRecord(
                source_filename, row_number, "record_is_not_an_object", source_record
            ))
            continue
        try:
            canonical, warnings, product_identity = _canonical_row(
                raw,
                schema=schema,
                source_filename=source_filename,
                source_row_number=row_number,
                naive_timezone=effective_naive_timezone,
            )
        except (ValueError, TypeError) as exc:
            quarantined.append(QuarantinedRecord(
                source_filename, row_number, str(exc), source_record
            ))
            continue

        product_conflict = state.register_product(
            product_identity,
            canonical["product_name"] or "",
            canonical["category"] or "",
        )
        if product_conflict:
            quarantined.append(QuarantinedRecord(
                source_filename, row_number, product_conflict, source_record
            ))
            continue

        primary_identity = (
            canonical["marketplace"] or "",
            canonical["product_id"] or "",
            canonical["seller_id"] or "",
            _identity_text(canonical["product_condition"]) or "unknown",
        )
        source_listing_id = (
            _text(raw.get("listing_id"))
            if schema in ("canonical", "enriched")
            else ""
        )
        if source_listing_id:
            listing_conflict = state.register_source_listing_id(
                source_listing_id, primary_identity
            )
            if listing_conflict:
                quarantined.append(QuarantinedRecord(
                    source_filename, row_number, listing_conflict, source_record
                ))
                continue

        nested_count = 0
        nested: list[Mapping[str, Any]] = []
        if schema == "amazon" and _text(raw.get("other_sellers_prices")):
            if policy == NestedOfferPolicy.REJECT_IF_PRESENT:
                quarantined.append(QuarantinedRecord(
                    source_filename,
                    row_number,
                    "nested_seller_offers_present_by_policy",
                    source_record,
                ))
                continue
            try:
                nested = _nested_offers(raw)
                nested_count = len(nested)
            except ValueError as exc:
                if policy == NestedOfferPolicy.EXPAND:
                    quarantined.append(QuarantinedRecord(
                        source_filename, row_number, str(exc), source_record
                    ))
                    continue
                warnings = tuple((*warnings, "nested_seller_payload_not_parsed"))

        source_identity = (
            source_filename,
            str(row_number),
            canonical["timestamp"] or "",
        )
        observation_key = (
            *primary_identity,
            canonical["timestamp"] or "",
        )
        observation_fingerprint = _stable_id((
            json.dumps(canonical, ensure_ascii=False, sort_keys=True),
            json.dumps(
                {
                    key: _preserved_text(raw.get(key))
                    for key in ENRICHMENT_COLUMNS
                } if schema == "enriched" else {},
                ensure_ascii=False,
                sort_keys=True,
            ),
        ))
        observation_conflict = state.register_observation(
            observation_key,
            observation_fingerprint,
        )
        if observation_conflict:
            quarantined.append(QuarantinedRecord(
                source_filename,
                row_number,
                observation_conflict,
                source_record,
            ))
            continue
        provenance = {
            "source_filename": source_filename,
            "source_row_number": row_number,
            "source_capture_timestamp": _text(raw.get("timestamp")) or None,
            "source_timezone": (
                ENRICHED_SOURCE_TIMEZONE if schema == "enriched" else None
            ),
            "source_last_updated": _preserved_text(raw.get("last_updated")),
            "source_type": canonical["source_type"],
            "synthetic": (
                _identity_text(canonical["source_type"]) == "synthetic"
                or _identity_text(raw.get("source_type")) == "synthetic"
            ),
            "nested_offer_policy": policy.value,
            "nested_offer_count": nested_count,
            "source_discount": raw.get("discount"),
            "source_coupon_discount": (
                _preserved_text(raw.get("coupon_discount"))
                if schema == "enriched" else None
            ),
            "rating_interpretation": (
                "individual_review_rating" if schema == "enriched" else None
            ),
        }
        enrichment = (
            {
                key: _preserved_text(raw.get(key))
                for key in ENRICHMENT_COLUMNS
            }
            if schema == "enriched"
            else {}
        )
        if schema == "enriched":
            raw_rating = _preserved_text(raw.get("rating"))
            if raw_rating is not None:
                try:
                    rating = _as_decimal(raw_rating)
                except ValueError:
                    warnings = tuple((*warnings, "invalid_enrichment_rating"))
                else:
                    if rating is not None and not Decimal(0) <= rating <= Decimal(5):
                        warnings = tuple((*warnings, "invalid_enrichment_rating"))
        records.append(AdaptedRecord(
            canonical=canonical,
            product_identity=product_identity,
            listing_identity=primary_identity,
            observation_identity="obs_" + _stable_id(observation_key),
            provenance=provenance,
            source_record=source_record,
            enrichment=enrichment,
            warnings=warnings,
        ))

        if policy != NestedOfferPolicy.EXPAND or not nested:
            continue

        candidates: list[tuple[int, Mapping[str, Any], dict[str, str | None], tuple[str, ...]]] = []
        for offer_index, offer in enumerate(nested, start=1):
            try:
                offer_canonical, offer_warnings, offer_product_identity = _canonical_row(
                    raw,
                    schema=schema,
                    source_filename=source_filename,
                    source_row_number=row_number,
                    naive_timezone=effective_naive_timezone,
                    offer=offer,
                )
                if offer_product_identity != product_identity:
                    raise ValueError("nested_offer_product_identity_mismatch")
                candidates.append((
                    offer_index,
                    offer,
                    offer_canonical,
                    offer_warnings,
                ))
            except (ValueError, TypeError) as exc:
                quarantined.append(QuarantinedRecord(
                    source_filename,
                    row_number,
                    f"nested_offer:{exc}",
                    source_record,
                    offer_index,
                ))

        counts: dict[tuple[str, str, str, str], int] = {}
        for _, _, offer_canonical, _ in candidates:
            identity = (
                offer_canonical["marketplace"] or "",
                offer_canonical["product_id"] or "",
                offer_canonical["seller_id"] or "",
                _identity_text(offer_canonical["product_condition"]) or "unknown",
            )
            counts[identity] = counts.get(identity, 0) + 1
        counts[primary_identity] = counts.get(primary_identity, 0) + 1

        for offer_index, offer, offer_canonical, offer_warnings in candidates:
            identity = (
                offer_canonical["marketplace"] or "",
                offer_canonical["product_id"] or "",
                offer_canonical["seller_id"] or "",
                _identity_text(offer_canonical["product_condition"]) or "unknown",
            )
            if counts[identity] > 1:
                quarantined.append(QuarantinedRecord(
                    source_filename,
                    row_number,
                    "ambiguous_nested_listing_identity",
                    source_record,
                    offer_index,
                ))
                continue
            listing_id = "lst_" + _stable_id(identity)
            offer_canonical["listing_id"] = listing_id
            offer_provenance = dict(provenance)
            offer_provenance.update({
                "source_offer_index": offer_index,
                "nested_offer_policy": policy.value,
                "seller_identity_source": (
                    "seller_url" if _text(offer.get("seller_url"))
                    else "unresolved"
                ),
            })
            records.append(AdaptedRecord(
                canonical=offer_canonical,
                product_identity=product_identity,
                listing_identity=identity,
                observation_identity="obs_" + _stable_id((
                    *source_identity,
                    *identity,
                    str(offer_index),
                )),
                provenance=offer_provenance,
                source_record=source_record,
                warnings=offer_warnings,
            ))

    return AdaptationResult(tuple(records), tuple(quarantined))


def adapt_csv_file(
    path: str | Path,
    *,
    naive_timezone: str | tzinfo | None = None,
    nested_offer_policy: NestedOfferPolicy = NestedOfferPolicy.PRESERVE_ONLY,
    context: AdapterContext | None = None,
) -> AdaptationResult:
    """Read one supported CSV and adapt it; no DB access or writes are performed."""
    csv_path = Path(path)
    filename = csv_path.name
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {filename}")
        issues = validate_source_headers(filename, reader.fieldnames)
        if issues:
            raise ValueError(f"invalid source header for {filename}: {', '.join(issues)}")
        rows = list(reader)
    return adapt_records(
        rows,
        filename,
        naive_timezone=naive_timezone,
        nested_offer_policy=nested_offer_policy,
        context=context,
    )


def adapt_csv_content(
    content: str,
    filename: str,
    *,
    naive_timezone: str | tzinfo | None = None,
    nested_offer_policy: NestedOfferPolicy = NestedOfferPolicy.PRESERVE_ONLY,
    context: AdapterContext | None = None,
) -> AdaptationResult:
    """Adapt CSV request content using the same validation as file input."""
    source_name = Path(filename).name
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if reader.fieldnames is None:
        raise ValueError(f"CSV has no header: {source_name}")
    issues = validate_source_headers(source_name, reader.fieldnames)
    if issues:
        raise ValueError(
            f"invalid source header for {source_name}: {', '.join(issues)}"
        )
    return adapt_records(
        list(reader),
        source_name,
        naive_timezone=naive_timezone,
        nested_offer_policy=nested_offer_policy,
        context=context,
    )
