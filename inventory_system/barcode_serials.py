import re


UNIT_NUMBER_DIGITS = 3
MAX_UNIT_NUMBER = (10 ** UNIT_NUMBER_DIGITS) - 1

_UNIT_BARCODE_PATTERN = re.compile(
    rf"^(?P<item_code>.+)-(?P<unit_number>\d{{{UNIT_NUMBER_DIGITS}}})$"
)


def normalize_scanned_barcode(value):
    """
    バーコードリーダーから受け取った文字列をSHARKの商品コード形式へ整える。

    旧形式の「案件コード_商品コード」と、新形式の
    「案件コード_商品コード-001」の両方に対応する。
    """
    barcode_text = str(value or "").strip()

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

    旧バーコードなど末尾3桁の個体連番が無い場合、個体連番はNone。
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
