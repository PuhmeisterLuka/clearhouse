"""Which stage deals with each injected defect kind, for the stage tests."""

PARSE_QUARANTINES = {"bad_date", "missing_sku", "missing_qty"}
PARSE_REPAIRS = {"sku_variant", "email_noise", "mojibake", "qty_text", "buyer_code_noise", "euro_price_variant"}
CHECK_QUARANTINES = {"future_date", "negative_qty", "unknown_sku"}
CHECK_REPAIRS = {"missing_currency", "duplicate_row", "total_mismatch", "price_cents_error"}
