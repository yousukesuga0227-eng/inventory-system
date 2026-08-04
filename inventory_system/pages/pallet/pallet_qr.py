"""パレットA4用の情報入りQR生成・従来コード互換解析。"""

from urllib.parse import quote, unquote


QR_PREFIX = "SHARKPAL1"


def _encode(value):
    return quote(str(value or ""), safe="-_.~")


def build_pallet_qr_payload(
    pallet_code,
    item_code="",
    customer_code="",
    customer_name="",
    category_name="",
    item_name="",
    quantity="",
    management_number="",
    receipt_code="",
    batch_code="",
    pallet_sequence="",
):
    fields = [
        ("PC", pallet_code),
        ("IC", item_code),
        ("CC", customer_code),
        ("CN", customer_name),
        ("CAT", category_name),
        ("IN", item_name),
        ("QTY", quantity),
        ("NO", management_number),
        ("RC", receipt_code),
        ("BC", batch_code),
        ("PS", pallet_sequence),
    ]
    parts = [QR_PREFIX]

    for key, value in fields:
        if value not in (None, ""):
            parts.append(f"{key}={_encode(value)}")

    return "|".join(parts)


def parse_pallet_qr_payload(value):
    text = str(value or "").strip()

    if not text.upper().startswith(QR_PREFIX + "|"):
        return {"pallet_code": text}

    result = {}

    for part in text.split("|")[1:]:
        if "=" not in part:
            continue

        key, encoded = part.split("=", 1)
        result[key.upper()] = unquote(encoded)

    result["pallet_code"] = result.get("PC", "")
    return result


def extract_pallet_code(value):
    parsed = parse_pallet_qr_payload(value)
    return str(parsed.get("pallet_code") or "").strip().upper()
