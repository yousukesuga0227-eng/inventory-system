import json
import re

from item_code_qr import extract_item_code_from_qr


UNIT_NUMBER_DIGITS = 3
MAX_UNIT_NUMBER = (10 ** UNIT_NUMBER_DIGITS) - 1

_UNIT_BARCODE_PATTERN = re.compile(
    rf"^(?P<item_code>.+)-(?P<unit_number>\d{{{UNIT_NUMBER_DIGITS}}})$"
)


def _extract_json_item_code(value):
    """将来JSON形式のQRを使っても商品コードを拾えるようにする。"""
    text = str(value or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    item_code = (
        data.get("item_code")
        or data.get("item")
        or data.get("code")
    )
    if not item_code:
        return None

    unit_number = data.get("unit_number") or data.get("unit")
    if unit_number is not None and str(unit_number).isdigit():
        return f"{str(item_code).strip()}-{int(unit_number):03d}"
    return str(item_code).strip()


def normalize_scanned_barcode(value):
    """
    バーコードリーダー／QRリーダーの入力をSHARKの商品コード形式へ整える。

    対応形式:
    - SHARK1|TYPE=ITEM|ITEM=... の情報入りQR
    - JSON形式のQR（将来互換）
    - 旧形式の「案件コード_商品コード」
    - 商品コードそのもの
    - 商品コード末尾に3桁の個体番号を付けた形式
    """
    barcode_text = str(value or "").strip()

    qr_item_code = extract_item_code_from_qr(barcode_text)
    if qr_item_code:
        return qr_item_code

    json_item_code = _extract_json_item_code(barcode_text)
    if json_item_code:
        return json_item_code

    if "_" in barcode_text:
        barcode_text = barcode_text.split("_")[-1]

    return barcode_text


def make_unit_barcode(item_code, unit_number):
    """商品コードへ3桁の個体連番を付ける。"""
    base_code = normalize_scanned_barcode(item_code)

    if not base_code:
        raise ValueError("商品コードが空です。")

    unit_number = int(unit_number)

    if not 1 <= unit_number <= MAX_UNIT_NUMBER:
        raise ValueError(
            f"個体連番は1～{MAX_UNIT_NUMBER}で指定してください。"
        )

    return f"{base_code}-{unit_number:0{UNIT_NUMBER_DIGITS}d}"


def split_unit_barcode(value):
    """
    戻り値は (商品コード本体, 個体連番)。

    末尾3桁の個体連番が無い場合、個体連番はNone。
    商品コード本体の末尾4桁連番とは混同しない。
    """
    barcode_text = normalize_scanned_barcode(value)
    match = _UNIT_BARCODE_PATTERN.fullmatch(barcode_text)

    if not match:
        return barcode_text, None

    return (
        match.group("item_code"),
        int(match.group("unit_number")),
    )


def is_unit_barcode(value):
    """末尾に3桁の個体連番が付いているかを返す。"""
    _, unit_number = split_unit_barcode(value)
    return unit_number is not None


def format_unit_numbers(unit_numbers, max_display=12):
    """個体番号の一覧を画面表示向けの短い文字列へ整える。"""
    numbers = sorted({int(number) for number in unit_numbers})

    if not numbers:
        return "-"

    visible = numbers[:max_display]
    text = ", ".join(
        f"{number:0{UNIT_NUMBER_DIGITS}d}"
        for number in visible
    )

    hidden_count = len(numbers) - len(visible)

    if hidden_count > 0:
        text += f" ...（残り{hidden_count}件）"

    return text
