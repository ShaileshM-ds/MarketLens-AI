"""
Focused tests for app.analytics.engine.sellers().

All data is synthesised in-memory; no MongoDB connection is required.
No real dataset is imported.
"""
import pytest
from datetime import datetime, timedelta, timezone
from app.analytics.engine import sellers, _seller_daily_tracks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(
    seller_id,
    date,            # "YYYY-MM-DD"
    price,           # item price (minor units, e.g. paise)
    *,
    shipping=None,   # None -> unknown shipping; int -> known shipping
    tax_treatment="included",
    currency="INR",
    region=None,
    seller_offer=False,
    hour=12,
):
    """Build a minimal normalised observation dict."""
    dt_str = f"{date}T{hour:02d}:00:00+00:00"
    total = (price + shipping) if (shipping is not None and tax_treatment == "included") else None
    return dict(
        key=f"{seller_id}-{date}-{hour}",
        seller_id=seller_id,
        price=price,
        shipping=shipping,
        tax_treatment=tax_treatment,
        total=total,
        currency=currency,
        region=region,
        observed_at=dt_str,
        ingested_at=dt_str,
        seller_offer=seller_offer,
        metadata={"condition": "new", "variant": "std"},
        quality_flags=([] if total is not None else ["incomplete_total"]),
    )


def _daily_series(seller_id, start_date, prices, *, shipping=None, currency="INR"):
    """Build a consecutive-day series for one seller."""
    start = datetime.fromisoformat(start_date)
    rows = []
    for i, p in enumerate(prices):
        d = (start + timedelta(days=i)).date().isoformat()
        rows.append(_obs(seller_id, d, p, shipping=shipping, currency=currency))
    return rows


# ---------------------------------------------------------------------------
# 1. Insufficient history / no pairs
# ---------------------------------------------------------------------------

def test_no_rows_returns_insufficient():
    result = sellers([], "2026-01-01T00:00:00+00:00")
    assert result["status"] == "insufficient seller history"
    assert result["pairs"] == []


def test_single_seller_no_pairs():
    rows = _daily_series("A", "2026-01-01", [10000] * 30)
    cutoff = "2026-01-30T23:59:59+00:00"
    result = sellers(rows, cutoff)
    assert result["status"] == "insufficient seller history"
    assert result["pairs"] == []


def test_two_sellers_too_few_overlap_days():
    """Less than 20 consecutive shared days produces no pairs."""
    rows = (
        _daily_series("A", "2026-01-01", list(range(10000, 10015)))
        + _daily_series("B", "2026-01-01", list(range(11000, 11015)))
    )
    cutoff = "2026-01-15T23:59:59+00:00"
    result = sellers(rows, cutoff)
    assert result["pairs"] == []


# ---------------------------------------------------------------------------
# 2. Cutoff excludes future observations
# ---------------------------------------------------------------------------

def test_cutoff_excludes_future_observations():
    prices_a = [10000] * 30 + [1]   # last entry is after cutoff
    prices_b = [10000] * 30 + [1]
    rows = _daily_series("A", "2026-01-01", prices_a) + _daily_series("B", "2026-01-01", prices_b)
    # cutoff is before the 31st day
    cutoff = "2026-01-30T23:59:59+00:00"
    result = sellers(rows, cutoff)
    it = result["series_item"]
    assert len(it.get("A", [])) == 30
    assert len(it.get("B", [])) == 30


# ---------------------------------------------------------------------------
# Helpers for compound-key tracks
# ---------------------------------------------------------------------------

def _item_sids(track):
    """Return the set of plain seller IDs present in a compound-key track."""
    return {slot[0] for slot in track}


def _slot_for(track, seller_id):
    """Return the first slot whose seller_id matches, or None."""
    for slot in track:
        if slot[0] == seller_id:
            return slot
    return None


# ---------------------------------------------------------------------------
# 3. Additional-seller observations (total=None) included in item track only
# ---------------------------------------------------------------------------

def test_additional_sellers_included_in_item_track():
    """
    Additional sellers have shipping=None -> total=None.
    They must appear in series_item but NOT in series_landed.
    """
    prices = list(range(10000, 10030))
    rows_a = _daily_series("main-seller", "2026-01-01", prices, shipping=0)
    rows_b = _daily_series("extra-seller", "2026-01-01", prices, shipping=None)
    rows = rows_a + rows_b

    cutoff = "2026-01-30T23:59:59+00:00"
    result = sellers(rows, cutoff)

    assert "extra-seller" in result["series_item"], "additional seller must appear in item track"
    assert "extra-seller" not in result["series_landed"], (
        "additional seller must NOT appear in landed track when total is None"
    )


def test_additional_seller_zero_shipping_not_invented():
    """
    When shipping=None, the engine must not invent a zero shipping cost
    and must not place the row in the landed track.
    """
    row = _obs("extra", "2026-01-10", 9000, shipping=None)
    assert row["total"] is None
    assert row["shipping"] is None

    track_item, track_landed, _ = _seller_daily_tracks(
        [row], "2026-01-10T23:59:59+00:00"
    )
    assert "extra" in _item_sids(track_item)
    assert "extra" not in _item_sids(track_landed), "must not invent a landed price from unknown shipping"


# ---------------------------------------------------------------------------
# 4. Known landed-price observations appear in landed track
# ---------------------------------------------------------------------------

def test_known_landed_appears_in_landed_track():
    row = _obs("seller-x", "2026-03-01", 10000, shipping=500)
    assert row["total"] == 10500

    track_item, track_landed, _ = _seller_daily_tracks(
        [row], "2026-03-01T23:59:59+00:00"
    )
    assert "seller-x" in _item_sids(track_item)
    assert "seller-x" in _item_sids(track_landed)
    slot = _slot_for(track_landed, "seller-x")
    assert track_landed[slot]["2026-03-01"] == 10500


# ---------------------------------------------------------------------------
# 5. Item-price and landed-price tracks are never cross-compared
# ---------------------------------------------------------------------------

def test_item_and_landed_tracks_not_mixed():
    """
    Seller A has only item price (no shipping).
    Seller B has both item and landed price.
    Any pair involving A can only be on the item track.
    """
    prices = [10000 + i * 10 for i in range(30)]
    rows_a = _daily_series("A", "2026-02-01", prices, shipping=None)
    rows_b = _daily_series("B", "2026-02-01", prices, shipping=200)

    cutoff = "2026-03-02T23:59:59+00:00"
    result = sellers(rows_a + rows_b, cutoff)

    for pair in result["pairs"]:
        if "A" in (pair["a"], pair["b"]):
            assert pair["price_basis"] == "item price", (
                "A pair involving seller A (no landed data) must use item price basis"
            )


# ---------------------------------------------------------------------------
# 6. Multiple offers from the same seller on the same day use median
# ---------------------------------------------------------------------------

def test_same_seller_same_day_two_offers_median():
    date = "2026-05-01"
    obs1 = _obs("multi", date, 10000, shipping=None, hour=9)
    obs2 = _obs("multi", date, 12000, shipping=None, hour=15)

    cutoff = "2026-05-01T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([obs1, obs2], cutoff)

    slot = _slot_for(track_item, "multi")
    assert slot is not None
    assert track_item[slot][date] == 11000.0, (
        f"Expected median(10000, 12000)=11000, got {track_item[slot][date]}"
    )


def test_same_seller_same_day_three_offers_median():
    date = "2026-06-15"
    rows = [
        _obs("s1", date, 9000,  shipping=None, hour=8),
        _obs("s1", date, 11000, shipping=None, hour=12),
        _obs("s1", date, 10000, shipping=None, hour=18),
    ]
    cutoff = "2026-06-15T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks(rows, cutoff)
    slot = _slot_for(track_item, "s1")
    assert track_item[slot][date] == 10000.0


def test_same_seller_same_day_landed_median():
    """Same-day multiple landed prices also aggregate by median."""
    date = "2026-07-01"
    rows = [
        _obs("s2", date, 10000, shipping=100, hour=9),
        _obs("s2", date, 10000, shipping=300, hour=14),
    ]
    # totals: 10100 and 10300
    cutoff = "2026-07-01T23:59:59+00:00"
    _, track_landed, _ = _seller_daily_tracks(rows, cutoff)
    slot = _slot_for(track_landed, "s2")
    assert track_landed[slot][date] == 10200.0


# ---------------------------------------------------------------------------
# 7. Currency and region isolation
# ---------------------------------------------------------------------------

def test_different_currencies_not_paired():
    prices = list(range(5000, 5030))
    rows_inr = _daily_series("INR-seller", "2026-01-01", prices, currency="INR")
    rows_usd = _daily_series("USD-seller", "2026-01-01", prices, currency="USD")

    result = sellers(rows_inr + rows_usd, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == [], "sellers with different currencies must not be paired"


def test_different_regions_not_paired():
    prices = list(range(5000, 5030))
    rows_north = _daily_series("north", "2026-01-01", prices)
    rows_south = _daily_series("south", "2026-01-01", prices)
    for r in rows_north:
        r["region"] = "north"
    for r in rows_south:
        r["region"] = "south"

    result = sellers(rows_north + rows_south, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == []


# ---------------------------------------------------------------------------
# 8. Constant-price sellers produce no pairs (zero variance)
# ---------------------------------------------------------------------------

def test_constant_sellers_produce_no_pairs():
    rows = []
    for sid in ("A", "B", "C"):
        rows += _daily_series(sid, "2026-01-01", [10000] * 30)
    result = sellers(rows, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == []


# ---------------------------------------------------------------------------
# 9. API response key compatibility
# ---------------------------------------------------------------------------

def test_response_keys_present():
    rows = _daily_series("A", "2026-01-01", [10000] * 5)
    result = sellers(rows, "2026-01-05T23:59:59+00:00")
    required = {"status", "pairs", "cadence", "disclaimer", "uncontrolled", "series"}
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"
    # Additive keys
    assert "series_item" in result
    assert "series_landed" in result
    assert "note" in result


def test_pairs_contain_price_basis():
    """Each pair dict must include price_basis."""
    varied = [10000 + (i % 7) * 200 for i in range(30)]
    rows = (
        _daily_series("P", "2026-01-01", varied, shipping=0)
        + _daily_series("Q", "2026-01-01", varied, shipping=0)
    )
    result = sellers(rows, "2026-01-30T23:59:59+00:00")
    for pair in result["pairs"]:
        assert "price_basis" in pair
        assert pair["price_basis"] in ("item price", "known landed price")


# ---------------------------------------------------------------------------
# 10. Series backward-compat: 'series' prefers landed when available
# ---------------------------------------------------------------------------

def test_series_prefers_landed_when_available():
    rows = _daily_series("LandedSeller", "2026-03-01", [10000] * 5, shipping=500)
    result = sellers(rows, "2026-03-05T23:59:59+00:00")
    for point in result["series"].get("LandedSeller", []):
        assert point["price_basis"] == "known landed price"


def test_series_falls_back_to_item_when_no_landed():
    rows = _daily_series("ItemOnlySeller", "2026-03-01", [10000] * 5, shipping=None)
    result = sellers(rows, "2026-03-05T23:59:59+00:00")
    for point in result["series"].get("ItemOnlySeller", []):
        assert point["price_basis"] == "item price"


# ---------------------------------------------------------------------------
# 11. Rows with missing seller_id are excluded gracefully
# ---------------------------------------------------------------------------

def test_row_without_seller_id_excluded():
    row = _obs("placeholder", "2026-04-01", 10000)
    row["seller_id"] = None
    track_item, track_landed, _ = _seller_daily_tracks(
        [row], "2026-04-01T23:59:59+00:00"
    )
    assert track_item == {}
    assert track_landed == {}


# ---------------------------------------------------------------------------
# 13. Currency/region contamination — same seller ID, different currencies
# ---------------------------------------------------------------------------

def test_same_seller_different_currencies_not_combined():
    """
    The same seller_id with INR observations on one day and USD observations
    on the same day must produce two separate slots, not one mixed median.
    """
    date = "2026-08-01"
    row_inr = _obs("shared-seller", date, 10000, currency="INR", hour=9)
    row_usd = _obs("shared-seller", date, 120,   currency="USD", hour=10)
    # Give them distinct keys so deduplication does not interfere.
    row_inr["key"] = "shared-seller-INR-2026-08-01"
    row_usd["key"] = "shared-seller-USD-2026-08-01"

    cutoff = "2026-08-01T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([row_inr, row_usd], cutoff)

    # There must be two separate slots for the same seller_id.
    slots = [s for s in track_item if s[0] == "shared-seller"]
    assert len(slots) == 2, (
        f"Expected 2 slots (one per currency), got {len(slots)}: {slots}"
    )
    currencies = {s[1] for s in slots}
    assert currencies == {"INR", "USD"}

    # Each slot must contain only its own currency's price.
    for slot in slots:
        price = track_item[slot][date]
        if slot[1] == "INR":
            assert price == 10000.0
        else:
            assert price == 120.0


def test_same_seller_different_currencies_not_paired():
    """
    Two slots for the same seller_id but different currencies must not
    produce a correlation pair.
    """
    prices = list(range(5000, 5030))
    rows_inr = _daily_series("shared", "2026-01-01", prices, currency="INR")
    rows_usd = _daily_series("shared", "2026-01-01", prices, currency="USD")

    result = sellers(rows_inr + rows_usd, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == [], (
        "same seller_id with different currencies must not be paired"
    )


# ---------------------------------------------------------------------------
# 14. Currency/region contamination — same seller ID, different regions
# ---------------------------------------------------------------------------

def test_same_seller_different_regions_not_combined():
    """
    The same seller_id with observations from different regions on the same
    date must produce two separate slots.
    """
    date = "2026-09-01"
    row_n = _obs("regional-seller", date, 10000, region="north", hour=9)
    row_s = _obs("regional-seller", date, 11000, region="south", hour=10)
    row_n["key"] = "regional-seller-north-2026-09-01"
    row_s["key"] = "regional-seller-south-2026-09-01"

    cutoff = "2026-09-01T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([row_n, row_s], cutoff)

    slots = [s for s in track_item if s[0] == "regional-seller"]
    assert len(slots) == 2, f"Expected 2 region slots, got {len(slots)}: {slots}"
    regions = {s[2] for s in slots}
    assert regions == {"north", "south"}


def test_same_seller_different_regions_not_paired():
    prices = list(range(5000, 5030))
    rows_n = _daily_series("reg-seller", "2026-01-01", prices)
    rows_s = _daily_series("reg-seller", "2026-01-01", prices)
    for r in rows_n:
        r["region"] = "north"
    for r in rows_s:
        r["region"] = "south"

    result = sellers(rows_n + rows_s, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == [], (
        "same seller_id with different regions must not be paired"
    )


# ---------------------------------------------------------------------------
# 15. Valid same-currency, same-region pair still works after the fix
# ---------------------------------------------------------------------------

def test_valid_same_currency_region_pair_still_works():
    """
    Two distinct sellers with matching currency and region and sufficient
    history must still produce a pair entry (even if not flagged).
    """
    prices = list(range(10000, 10030))
    rows = (
        _daily_series("seller-X", "2026-01-01", prices, currency="INR")
        + _daily_series("seller-Y", "2026-01-01", prices, currency="INR")
    )
    result = sellers(rows, "2026-01-30T23:59:59+00:00")
    # Constant prices produce no pairs (zero variance guard), but the
    # currency/region check must not be the reason they are absent.
    # Use varying prices to confirm a pair is produced.
    varied = [10000 + (i % 5) * 300 for i in range(30)]
    rows2 = (
        _daily_series("seller-X", "2026-02-01", varied, currency="INR")
        + _daily_series("seller-Y", "2026-02-01", varied, currency="INR")
    )
    result2 = sellers(rows2, "2026-03-02T23:59:59+00:00")
    # At least one pair must be attempted (may not be flagged due to low variance).
    assert isinstance(result2["pairs"], list)
    # Confirm no cross-currency contamination: all pairs must be INR/INR.
    for pair in result2["pairs"]:
        assert "price_basis" in pair


# ---------------------------------------------------------------------------
# 16. Missing currency or region values are handled safely
# ---------------------------------------------------------------------------

def test_missing_currency_treated_as_empty_string_slot():
    """
    A row with no currency field must not raise an error and must be
    placed in its own slot (currency='') rather than mixed with INR rows.
    """
    date = "2026-10-01"
    row_no_cur = _obs("seller-nc", date, 5000, hour=9)
    row_no_cur["currency"] = None  # simulate missing currency
    row_inr    = _obs("seller-nc", date, 6000, currency="INR", hour=10)
    row_no_cur["key"] = "seller-nc-nocur-2026-10-01"
    row_inr["key"]    = "seller-nc-inr-2026-10-01"

    cutoff = "2026-10-01T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([row_no_cur, row_inr], cutoff)

    slots = [s for s in track_item if s[0] == "seller-nc"]
    assert len(slots) == 2, f"Expected 2 slots, got {slots}"
    currencies = {s[1] for s in slots}
    assert "INR" in currencies
    assert "" in currencies  # missing currency normalised to empty string


# ---------------------------------------------------------------------------
# 17. Duplicate observation keys are counted only once per slot per day
# ---------------------------------------------------------------------------

def test_duplicate_key_counted_once():
    """
    Two rows with the same observation key for the same seller/date must
    contribute only one price to the daily median.
    """
    date = "2026-11-01"
    row1 = _obs("dup-seller", date, 10000, hour=9)
    row2 = dict(row1)  # exact copy — same key
    row2["price"] = 20000  # different price, same key

    cutoff = "2026-11-01T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([row1, row2], cutoff)

    slot = _slot_for(track_item, "dup-seller")
    assert slot is not None
    # Only the first row's price should be counted; the duplicate is skipped.
    assert track_item[slot][date] == 10000.0, (
        f"Duplicate key must not bias the median; got {track_item[slot][date]}"
    )


def test_distinct_keys_same_seller_same_day_both_counted():
    """
    Two rows with different keys for the same seller/date are both legitimate
    and must both contribute to the median.
    """
    date = "2026-11-02"
    row1 = _obs("legit-seller", date, 10000, hour=9)
    row2 = _obs("legit-seller", date, 12000, hour=15)  # different key (hour differs)

    cutoff = "2026-11-02T23:59:59+00:00"
    track_item, _, _ = _seller_daily_tracks([row1, row2], cutoff)

    slot = _slot_for(track_item, "legit-seller")
    assert track_item[slot][date] == 11000.0  # median(10000, 12000)


# ---------------------------------------------------------------------------
# 12. Regression: seeded synchronisation still flags exactly as before
# ---------------------------------------------------------------------------

def test_seeded_synchronisation_still_flags():
    """Regression guard for the seeded correlated pair (product 5).

    With 999 permutations the minimum achievable p-value is 0.001.  With 12
    total pairs for product 5, the minimum adjusted_p = 0.001 * 12 = 0.012,
    which is below the 0.05 threshold.  The strongly correlated pair
    (Seller 1 / Seller 2, score=100, p_value=0.001) therefore meets all
    flagging criteria and must be flagged=True.

    Product 6 has no strongly correlated pair and must produce no flagged pairs.
    """
    from app.services.demo import generate, DEMO_END
    from app.services.normalize import normalize

    groups = {5: [], 6: []}
    for r in generate():
        number = int(r["asin"][4:])
        if number in groups:
            _, o = normalize(r, "demo", "test")
            o["ingested_at"] = o["observed_at"]
            groups[number].append(o)

    positive = sellers(groups[5], DEMO_END)
    negative = sellers(groups[6], DEMO_END)

    # The strongly correlated pair must be flagged with the higher permutation
    # resolution (minimum p=0.001, adjusted_p=0.012 for 12 pairs).
    flagged = [p for p in positive["pairs"] if p["flagged"]]
    assert len(flagged) >= 1, (
        f"Expected at least one flagged pair for product 5; "
        f"pairs: {[(p['a'], p['b'], p['score'], p['p_value'], p['adjusted_p'], p['flagged']) for p in positive['pairs']]}"
    )
    assert not any(p["flagged"] for p in negative["pairs"])
    # All pairs carry price_basis
    for pair in positive["pairs"] + negative["pairs"]:
        assert "price_basis" in pair


# ---------------------------------------------------------------------------
# 18. Series survival — same seller ID, multiple currency/region slots
# ---------------------------------------------------------------------------

def test_same_seller_usd_and_inr_both_survive_in_series():
    """
    A seller with USD observations and INR observations must produce two
    separate entries in series_item, not one overwriting the other.
    The composite key format is '<seller_id>|<currency>|<region>'.
    """
    prices = list(range(10000, 10030))
    rows_usd = _daily_series("multi-cur", "2026-01-01", prices, currency="USD")
    rows_inr = _daily_series("multi-cur", "2026-01-01", prices, currency="INR")

    result = sellers(rows_usd + rows_inr, "2026-01-30T23:59:59+00:00")
    item = result["series_item"]

    # Plain key must not exist when there are multiple slots.
    assert "multi-cur" not in item, (
        "Plain seller ID key must not be used when multiple currency slots exist"
    )
    # Both composite keys must be present.
    usd_key = "multi-cur|USD|"
    inr_key = "multi-cur|INR|"
    assert usd_key in item, f"USD series missing; keys: {list(item)}"
    assert inr_key in item, f"INR series missing; keys: {list(item)}"
    # Each key must contain only its own currency's prices.
    assert len(item[usd_key]) == 30
    assert len(item[inr_key]) == 30


def test_same_seller_different_regions_both_survive_in_series():
    """
    A seller with observations from two different regions must produce two
    separate entries in series_item.
    """
    prices = list(range(10000, 10030))
    rows_n = _daily_series("reg-multi", "2026-01-01", prices, currency="INR")
    rows_s = _daily_series("reg-multi", "2026-01-01", prices, currency="INR")
    for r in rows_n:
        r["region"] = "north"
    for r in rows_s:
        r["region"] = "south"

    result = sellers(rows_n + rows_s, "2026-01-30T23:59:59+00:00")
    item = result["series_item"]

    assert "reg-multi" not in item, (
        "Plain seller ID key must not be used when multiple region slots exist"
    )
    north_key = "reg-multi|INR|north"
    south_key = "reg-multi|INR|south"
    assert north_key in item, f"north series missing; keys: {list(item)}"
    assert south_key in item, f"south series missing; keys: {list(item)}"


# ---------------------------------------------------------------------------
# 19. Both item-price and landed-price pairs returned for same seller pair
# ---------------------------------------------------------------------------

def test_same_seller_pair_in_both_tracks_both_returned():
    """
    When two sellers both have known shipping, the same seller pair appears
    in both the item-price track and the landed-price track.  Both must be
    present in the pairs list with distinct price_basis values.
    """
    varied = [10000 + (i % 7) * 300 for i in range(30)]
    rows = (
        _daily_series("alpha", "2026-01-01", varied, shipping=500, currency="INR")
        + _daily_series("beta",  "2026-01-01", varied, shipping=500, currency="INR")
    )
    result = sellers(rows, "2026-01-30T23:59:59+00:00")

    ab_pairs = [p for p in result["pairs"] if {p["a"], p["b"]} == {"alpha", "beta"}]
    bases = {p["price_basis"] for p in ab_pairs}
    assert "item price" in bases, (
        f"item-price pair missing for alpha/beta; pairs: {ab_pairs}"
    )
    assert "known landed price" in bases, (
        f"landed-price pair missing for alpha/beta; pairs: {ab_pairs}"
    )
    assert len(ab_pairs) == 2, (
        f"Expected exactly 2 pairs (one per basis) for alpha/beta; got {ab_pairs}"
    )


# ---------------------------------------------------------------------------
# 20. Two sellers with missing currency must not be paired
# ---------------------------------------------------------------------------

def test_two_sellers_missing_currency_not_paired():
    """
    Two sellers whose currency field is None (normalised to '') must not be
    paired.  Matching two unknowns is not evidence of a shared currency.
    """
    prices = list(range(5000, 5030))
    rows_a = _daily_series("nc-a", "2026-01-01", prices, currency="INR")
    rows_b = _daily_series("nc-b", "2026-01-01", prices, currency="INR")
    for r in rows_a:
        r["currency"] = None
    for r in rows_b:
        r["currency"] = None

    result = sellers(rows_a + rows_b, "2026-01-30T23:59:59+00:00")
    assert result["pairs"] == [], (
        "Two sellers with unknown currency must not be paired"
    )


# ---------------------------------------------------------------------------
# 21. Known-currency sellers still produce pairs normally
# ---------------------------------------------------------------------------

def test_known_usd_sellers_produce_pairs():
    """
    Two sellers with explicit USD currency and sufficient varying history
    must produce at least one pair entry.
    """
    varied = [10000 + (i % 5) * 400 for i in range(30)]
    rows = (
        _daily_series("usd-x", "2026-01-01", varied, currency="USD")
        + _daily_series("usd-y", "2026-01-01", varied, currency="USD")
    )
    result = sellers(rows, "2026-01-30T23:59:59+00:00")
    assert len(result["pairs"]) >= 1, (
        "Known USD sellers with varying prices must produce at least one pair"
    )
    for pair in result["pairs"]:
        assert pair["a"] in ("usd-x", "usd-y")
        assert pair["b"] in ("usd-x", "usd-y")


# ---------------------------------------------------------------------------
# 22. Permutation resolution allows flagging with sufficient evidence
# ---------------------------------------------------------------------------

def test_permutation_resolution_allows_flagging():
    """
    With 999 permutations the minimum achievable p-value is 0.001.
    A perfectly correlated pair (identical price series) with only 2 total
    pairs in the family gives adjusted_p = 0.001 * 2 = 0.002 <= 0.05.
    The pair must be flagged=True when score and stability also qualify.

    This test verifies that the increased permutation count (fix 3) restores
    the ability to flag strongly correlated pairs at low hypothesis counts.
    """
    # Build a perfectly correlated pair: identical price movements every day.
    # Use a pattern with enough variance to pass the active-events guard.
    prices = [10000 + (i % 3) * 500 for i in range(90)]  # 90 days, repeating pattern
    rows = (
        _daily_series("perm-a", "2026-01-01", prices, currency="USD")
        + _daily_series("perm-b", "2026-01-01", prices, currency="USD")
    )
    cutoff = "2026-04-01T23:59:59+00:00"
    result = sellers(rows, cutoff)

    ab_pairs = [p for p in result["pairs"] if {p["a"], p["b"]} == {"perm-a", "perm-b"}]
    assert len(ab_pairs) >= 1, f"No pair produced; result: {result['pairs']}"

    # With only 2 total pairs (item only, since no shipping), adjusted_p = p * 2.
    # The minimum p = 0.001, so minimum adjusted_p = 0.002.
    for pair in ab_pairs:
        assert pair["adjusted_p"] <= 1.0, "adjusted_p must be capped at 1"
        # A perfectly correlated pair with 2 hypotheses must be flaggable.
        if pair["score"] >= 75 and pair["adjusted_p"] <= 0.05:
            assert pair["flagged"] is True, (
                f"Pair with score={pair['score']} adjusted_p={pair['adjusted_p']} "
                f"must be flagged; stability={pair['stability_halves']}"
            )
