import json
import re

from item_code_qr import extract_item_code_from_qr


UNIT_NUMBER_DIGITS = 3
MAX_UNIT_NUMBER = (10 ** UNIT_NUMBER_DIGITS) - 1

_UNIT_BARCODE_PATTERN = re.compile(
    rf"^(?P<item_code>.+)-(?P<unit_number>\d{{{UNIT_NUMBER_DIGITS}}})$"
)
_QUANTITY_UNIT_QR_PATTERN = re.compile(
    r"^(?P<item_code>.+)\|(?P<sequence>\d+)/(?P<total>\d+)$"
)
_QUANTITY_UNIT_QR_SCAN_PATTERN = re.compile(
    r"^(?P<item_code>.+)(?P<separator>.)(?P<sequence>\d+)/(?P<total>\d+)$"
)
_QUANTITY_UNIT_QR_SEPARATORS = frozenset(
    {"|", "｜", "}", "｝", "\\", "＼", "]", "］"}
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


def _normalize_quantity_unit_qr_text(value):
    """実機リーダーで変換されたA4 QR区切り記号を半角|へ統一する。"""
    text = str(value or "").strip()
    match = _QUANTITY_UNIT_QR_SCAN_PATTERN.fullmatch(text)

    if not match:
        return None

    if match.group("separator") not in _QUANTITY_UNIT_QR_SEPARATORS:
        return None

    return (
        f"{match.group('item_code')}|"
        f"{match.group('sequence')}/{match.group('total')}"
    )


def normalize_scanned_barcode(value):
    """
    バーコードリーダー／QRリーダーの入力をSHARKの商品コード形式へ整える。

    対応形式:
    - SHARK1|TYPE=ITEM|ITEM=... の情報入りQR
    - JSON形式のQR（将来互換）
    - A4個別QR（実機で|が}等へ変換された入力を含む）
    - 商品コードそのもの
    - 商品コード末尾に3桁の個体番号を付けた形式

    アンダースコアは商品コードの一部として保持する。
    旧形式の「案件コード_商品コード」は、商品一覧と照合するときだけ
    item_code_candidates() で後方互換候補へ展開する。
    """
    barcode_text = str(value or "").strip()

    qr_item_code = extract_item_code_from_qr(barcode_text)
    if qr_item_code:
        return qr_item_code

    json_item_code = _extract_json_item_code(barcode_text)
    if json_item_code:
        return json_item_code

    quantity_unit_qr = _normalize_quantity_unit_qr_text(barcode_text)
    if quantity_unit_qr:
        return quantity_unit_qr

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

    A4商品票の「商品コード|通番/総数」は通番を個体連番として返す。
    末尾3桁の個体連番が無い場合、個体連番はNone。
    商品コード本体の末尾4桁連番とは混同しない。
    """
    barcode_text = normalize_scanned_barcode(value)

    quantity_match = _QUANTITY_UNIT_QR_PATTERN.fullmatch(barcode_text)
    if quantity_match:
        sequence = int(quantity_match.group("sequence"))
        total = int(quantity_match.group("total"))

        if total > 0 and 1 <= sequence <= total:
            return quantity_match.group("item_code"), sequence

    match = _UNIT_BARCODE_PATTERN.fullmatch(barcode_text)

    if not match:
        return barcode_text, None

    return (
        match.group("item_code"),
        int(match.group("unit_number")),
    )


def item_code_candidate_details(value):
    """QRから、(商品コード候補, 個体番号)を優先順で返す。"""
    barcode_text = normalize_scanned_barcode(value)
    base_code, unit_number = split_unit_barcode(barcode_text)
    details = []

    def append_candidate(code, candidate_unit_number):
        code = str(code or "")
        if not code:
            return
        candidate = (code, candidate_unit_number)
        if candidate not in details:
            details.append(candidate)

    # 商品コード自体がABC-001のように末尾3桁でも、完全一致を優先する。
    append_candidate(barcode_text, None)
    append_candidate(base_code, unit_number)

    # 旧形式「案件コード_商品コード」は完全一致を先に試し、
    # 見つからない場合だけアンダースコア区切りの後方を順に試す。
    # 商品コード自体にアンダースコアがあっても、長い候補が先になる。
    if "_" in base_code:
        parts = base_code.split("_")
        for index in range(1, len(parts)):
            legacy_code = "_".join(parts[index:]).strip()
            append_candidate(legacy_code, unit_number)

    return details


def item_code_candidates(value):
    """QRから、商品コード候補と個体番号を後方互換形式で返す。"""
    details = item_code_candidate_details(value)
    candidates = [code for code, _unit_number in details]
    unit_number = next(
        (
            candidate_unit_number
            for _code, candidate_unit_number in details
            if candidate_unit_number is not None
        ),
        None,
    )

    return candidates, unit_number


def _item_code_lookup_key(value):
    """DBとQRの商品コードを安全に照合するための内部キー。"""
    return str(value or "").strip().casefold()


def resolve_scanned_item_code(value, available_codes):
    """
    QRの商品コードを、DBに存在する元の商品コードへ解決する。

    前後空白・大文字小文字の差を吸収しつつ、商品コード内の
    アンダースコアを優先して保持する。見つからない場合は、
    エラー表示用にQRから得た第一候補を返す。
    """
    details = item_code_candidate_details(value)
    code_by_key = {}

    for code in available_codes or []:
        key = _item_code_lookup_key(code)
        if key and key not in code_by_key:
            code_by_key[key] = code

    for candidate, unit_number in details:
        matched_code = code_by_key.get(_item_code_lookup_key(candidate))
        if matched_code is not None:
            return matched_code, unit_number

    if not details:
        return "", None

    return details[0]


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
