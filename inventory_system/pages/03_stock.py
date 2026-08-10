import os
import re
from collections import Counter
from datetime import datetime
from io import BytesIO

import streamlit as st
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from auth import check_login
from barcode_serials import (
    format_unit_numbers,
    is_unit_barcode,
    normalize_scanned_barcode,
    split_unit_barcode,
)
from database import get_connection


check_login()
conn = get_connection()

st.title("📦 入出庫")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FONT_PATH = os.path.join(BASE_DIR, "fonts", "NotoSansJP-VariableFont_wght.ttf")
LOGO_PATH = os.path.join(BASE_DIR, "20260608-logo.png")
PDF_FONT = "Helvetica"

if os.path.exists(FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont("NotoSansJP", FONT_PATH))
        PDF_FONT = "NotoSansJP"
    except Exception:
        PDF_FONT = "Helvetica"


def row_to_dict(row):
    return dict(row) if row is not None else None


def safe_text(value):
    return "" if value is None else str(value)


def safe_filename(value):
    value = re.sub(r'[\\/:*?"<>|]+', "_", safe_text(value)).strip()
    return value or "document"


def chunked(values, size):
    return [values[index:index + size] for index in range(0, len(values), size)]


def rollback_connection(connection):
    try:
        if hasattr(connection, "rollback"):
            connection.rollback()
        elif hasattr(connection, "conn"):
            connection.conn.rollback()
    except Exception:
        pass


def project_company(project_id):
    try:
        row = conn.execute(
            """
            SELECT c.code, c.name
            FROM project_companies pc
            JOIN companies c ON c.id = pc.company_id
            WHERE pc.project_id = ?
            ORDER BY c.name
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return row_to_dict(row) or {"code": "", "name": ""}
    except Exception:
        return {"code": "", "name": ""}


def add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont(PDF_FONT, 8)
    canvas.drawRightString(
        landscape(A4)[0] - 8 * mm,
        5 * mm,
        f"{doc.page} / SHARK",
    )
    canvas.restoreState()


def create_project_document(title, project, company, items, include_barcode):
    """A4横・1ページ最大10商品で帳票を作る。"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=7 * mm,
        leftMargin=7 * mm,
        topMargin=6 * mm,
        bottomMargin=8 * mm,
    )

    styles = getSampleStyleSheet()
    # SHARK document header redesign 20260808
    title_style = ParagraphStyle(
        "SharkTitle",
        parent=styles["Title"],
        fontName=PDF_FONT,
        fontSize=20,
        leading=22,
        alignment=1,
        textColor=colors.HexColor("#222222"),
        spaceAfter=0,
    )
    body_style = ParagraphStyle(
        "SharkBody",
        parent=styles["BodyText"],
        fontName=PDF_FONT,
        fontSize=8,
        leading=9,
    )

    item_pages = chunked(items, 10) or [[]]
    elements = []

    for page_index, page_items in enumerate(item_pages):
        if page_index > 0:
            elements.append(PageBreak())

        # 左：ロゴ / 中央：帳票タイトル / 右：余白
        # 左右を同幅にすることでタイトルを用紙の真ん中に配置する。
        if os.path.exists(LOGO_PATH):
            logo = Image(LOGO_PATH)

            # 元画像の縦横比を維持しながら、指定枠内に縮小する。
            max_logo_width = 38 * mm
            max_logo_height = 10 * mm
            scale = min(
                max_logo_width / float(logo.imageWidth),
                max_logo_height / float(logo.imageHeight),
                1.0,
            )
            logo.drawWidth = float(logo.imageWidth) * scale
            logo.drawHeight = float(logo.imageHeight) * scale
            logo_cell = logo
        else:
            logo_cell = Paragraph("SHARK", body_style)

        document_title = Paragraph(
            f"【{safe_text(title)}】",
            title_style,
        )

        header = Table(
            [[logo_cell, document_title, ""]],
            colWidths=[55 * mm, 167 * mm, 55 * mm],
            rowHeights=[14 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.7, colors.HexColor("#666666")),
                ]
            )
        )
        elements.append(header)
        elements.append(Spacer(1, 2 * mm))

        info_data = [
            [
                "企業名",
                safe_text(company.get("name")),
                "企業コード",
                safe_text(company.get("code")),
            ],
            [
                "案件名",
                safe_text(project.get("name")),
                "案件コード",
                safe_text(project.get("code")),
            ],
            [
                "出荷予定日",
                safe_text(project.get("shipping_date")),
                "発行日",
                datetime.now().strftime("%Y/%m/%d"),
            ],
            [
                "発行者",
                st.session_state.get("display_name", st.session_state.username),
                "ページ商品数",
                f"{len(page_items)}品",
            ],
        ]

        info_table = Table(
            info_data,
            colWidths=[24 * mm, 112 * mm, 24 * mm, 117 * mm],
            rowHeights=[6 * mm] * 4,
        )
        info_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAEAEA")),
                    ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EAEAEA")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(info_table)
        elements.append(Spacer(1, 2 * mm))

        if include_barcode:
            table_data = [[
                "No.", "商品コード", "商品名", "数量", "バーコード", "ピッキング", "検品", "積込"
            ]]
            for index, item in enumerate(page_items, start=page_index * 10 + 1):
                barcode_obj = code128.Code128(
                    safe_text(item["code"]),
                    barHeight=8 * mm,
                    barWidth=0.34,
                    humanReadable=True,
                )
                table_data.append([
                    str(index),
                    safe_text(item["code"]),
                    Paragraph(safe_text(item["name"]), body_style),
                    str(item["quantity"]),
                    barcode_obj,
                    "□",
                    "□",
                    "□",
                ])

            item_table = Table(
                table_data,
                colWidths=[8 * mm, 32 * mm, 87 * mm, 15 * mm, 67 * mm, 24 * mm, 22 * mm, 22 * mm],
                rowHeights=[8 * mm] + [11 * mm] * len(page_items),
                repeatRows=1,
            )
        else:
            table_data = [["No.", "商品コード", "商品名", "数量", "備考"]]
            for index, item in enumerate(page_items, start=page_index * 10 + 1):
                table_data.append([
                    str(index),
                    safe_text(item["code"]),
                    Paragraph(safe_text(item["name"]), body_style),
                    str(item["quantity"]),
                    "",
                ])

            item_table = Table(
                table_data,
                colWidths=[10 * mm, 42 * mm, 142 * mm, 22 * mm, 61 * mm],
                rowHeights=[8 * mm] + [11 * mm] * len(page_items),
                repeatRows=1,
            )

        item_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAEAEA")),
                    ("ALIGN", (0, 0), (1, -1), "CENTER"),
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),
                    ("ALIGN", (4, 1), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        elements.append(item_table)
        elements.append(Spacer(1, 2 * mm))

        footer_table = Table(
            [["備考", ""], ["確認", "担当：　　　　　　　　　受領：　　　　　　　　　日付：　　　　年　　月　　日"]],
            colWidths=[22 * mm, 255 * mm],
            rowHeights=[8 * mm, 8 * mm],
        )
        footer_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAEAEA")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(footer_table)

    doc.build(elements, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return buffer.getvalue()


# ============================================================
# 共通：案件一覧
# ============================================================
projects = conn.execute(
    """
    SELECT id, code, name, shipping_date
    FROM projects
    WHERE COALESCE(is_hidden, FALSE) = FALSE
    ORDER BY name
    """
).fetchall()
projects = [row_to_dict(row) for row in projects]

if not projects:
    st.warning("先に案件を登録してください")
    conn.close()
    st.stop()

project_label_map = {
    f"{project['code']} - {project['name']}": project["id"]
    for project in projects
}
project_by_id = {project["id"]: project for project in projects}


# ============================================================
# セッション初期化（出庫）
# ============================================================
def init_session():
    defaults = {
        "stock_out_project_id": None,
        "stock_out_scanned_codes": [],
        "stock_out_barcode_input": "",
        "stock_out_notice": None,
        "stock_out_registered": False,
        "stock_out_documents": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session()


def clear_outbound_state():
    st.session_state.stock_out_scanned_codes = []
    st.session_state.stock_out_barcode_input = ""
    st.session_state.stock_out_notice = None
    st.session_state.stock_out_registered = False
    st.session_state.stock_out_documents = None


def add_outbound_barcode():
    barcode_text = normalize_scanned_barcode(
        st.session_state.stock_out_barcode_input
    )
    st.session_state.stock_out_barcode_input = ""

    if not barcode_text:
        return

    item_map = st.session_state.get("stock_out_item_map", {})
    required_map = st.session_state.get("stock_out_required_map", {})
    base_code, _unit_number = split_unit_barcode(barcode_text)

    if base_code not in item_map:
        st.session_state.stock_out_notice = (
            "error",
            f"案件違い：{base_code} は選択中の案件の商品ではありません。",
        )
        return

    if is_unit_barcode(barcode_text) and barcode_text in st.session_state.stock_out_scanned_codes:
        st.session_state.stock_out_notice = (
            "error",
            f"重複読取：{barcode_text} はすでに読み取り済みです。",
        )
        return

    current_counter = Counter(
        split_unit_barcode(code)[0]
        for code in st.session_state.stock_out_scanned_codes
    )
    if current_counter.get(base_code, 0) >= required_map.get(base_code, 0):
        st.session_state.stock_out_notice = (
            "error",
            f"読取超過：{base_code} は必要数に達しています。",
        )
        return

    st.session_state.stock_out_scanned_codes.append(barcode_text)
    st.session_state.stock_out_notice = (
        "success",
        f"読取完了：{barcode_text}",
    )


# ============================================================
# 画面
# ============================================================
tab_in, tab_out = st.tabs(["📥 入庫", "📤 出庫"])


# ============================================================
# 入庫
# ============================================================
with tab_in:
    st.subheader("入庫登録")
    st.caption("案件 → 商品 → 数量の順に登録します。")

    selected_project_label = st.selectbox(
        "案件",
        list(project_label_map.keys()),
        key="stock_in_project",
    )
    in_project_id = project_label_map[selected_project_label]

    in_items = conn.execute(
        """
        SELECT id, code, name
        FROM items
        WHERE project_id = ?
          AND COALESCE(is_active, TRUE) = TRUE
        ORDER BY code
        """,
        (in_project_id,),
    ).fetchall()
    in_items = [row_to_dict(row) for row in in_items]

    if not in_items:
        st.warning("この案件には商品が登録されていません")
    else:
        in_item_options = [
            f"{item['code']} - {item['name']}"
            for item in in_items
        ]
        in_item_map = {
            f"{item['code']} - {item['name']}": item
            for item in in_items
        }
        in_code_map = {
            safe_text(item["code"]): f"{item['code']} - {item['name']}"
            for item in in_items
        }

        barcode = st.text_input(
            "バーコード / 商品コード",
            placeholder="バーコードを読み取るか、商品コードを入力",
            key="stock_in_barcode",
        )

        default_index = 0
        if barcode:
            clean_barcode = normalize_scanned_barcode(barcode)
            base_code, unit_number = split_unit_barcode(clean_barcode)
            matched_label = in_code_map.get(base_code)
            if matched_label:
                default_index = in_item_options.index(matched_label)
                st.success(f"バーコード一致：{matched_label}")
                if unit_number is not None:
                    st.caption(f"個体No.：{unit_number:03d}")
            else:
                st.warning("バーコードに一致する商品がありません")

        selected_item_label = st.selectbox(
            "商品",
            in_item_options,
            index=default_index,
            key=f"stock_in_item_{in_project_id}",
        )
        selected_item = in_item_map[selected_item_label]

        current_stock = conn.execute(
            """
            SELECT COALESCE(SUM(qty), 0)
            FROM stock_logs
            WHERE project_id = ? AND item_id = ?
            """,
            (in_project_id, selected_item["id"]),
        ).fetchone()[0]
        st.info(f"現在庫：{current_stock}")

        in_qty = st.number_input(
            "入庫数量",
            min_value=1,
            step=1,
            key="stock_in_qty",
        )

        if st.button("✅ 入庫登録", type="primary", use_container_width=True):
            try:
                conn.execute(
                    """
                    INSERT INTO stock_logs(project_id, item_id, qty, type, username)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        in_project_id,
                        selected_item["id"],
                        int(in_qty),
                        "入庫",
                        st.session_state.username,
                    ),
                )
                conn.commit()
                st.success(f"入庫完了：{selected_item_label} × {int(in_qty)}")
            except Exception as error:
                rollback_connection(conn)
                st.error(f"入庫登録に失敗しました：{error}")


# ============================================================
# 出庫
# ============================================================
# === SHARK SIMPLE OUTBOUND 20260810 START ===
with tab_out:
    st.subheader("出庫処理")
    st.caption(
        "①案件選択 → ②出荷指示書でピッキング → "
        "③積込前にバーコード読取 → ④出庫確定 → ⑤納品書・受領書"
    )

    selected_out_label = st.selectbox(
        "案件選択",
        list(project_label_map.keys()),
        key="stock_out_project_select",
    )
    out_project_id = project_label_map[selected_out_label]
    out_project = project_by_id[out_project_id]
    out_company = project_company(out_project_id)

    if st.session_state.stock_out_project_id != out_project_id:
        st.session_state.stock_out_project_id = out_project_id
        clear_outbound_state()
        st.rerun()

    out_items = conn.execute(
        """
        SELECT id, code, name,
               COALESCE(required_quantity, 1) AS required_quantity
        FROM items
        WHERE project_id = ?
          AND COALESCE(is_active, TRUE) = TRUE
        ORDER BY code
        """,
        (out_project_id,),
    ).fetchall()
    out_items = [row_to_dict(row) for row in out_items]

    if not out_items:
        st.warning("この案件には有効な商品が登録されていません")
    else:
        for item in out_items:
            planned_qty = max(1, int(item["required_quantity"] or 1))
            shipped_row = conn.execute(
                """
                SELECT COALESCE(
                    SUM(
                        CASE
                            WHEN type = '出庫' AND qty < 0
                            THEN -qty
                            ELSE 0
                        END
                    ),
                    0
                )
                FROM stock_logs
                WHERE project_id = ? AND item_id = ?
                """,
                (out_project_id, item["id"]),
            ).fetchone()
            already_shipped = int(shipped_row[0] or 0)

            item["required_quantity"] = planned_qty
            item["already_shipped"] = already_shipped
            item["remaining_quantity"] = max(
                planned_qty - already_shipped,
                0,
            )

        out_item_map = {
            safe_text(item["code"]): item
            for item in out_items
        }
        required_map = {
            safe_text(item["code"]): item["remaining_quantity"]
            for item in out_items
        }
        st.session_state.stock_out_item_map = out_item_map
        st.session_state.stock_out_required_map = required_map

        instruction_items = [
            {
                "code": item["code"],
                "name": item["name"],
                "quantity": item["required_quantity"],
            }
            for item in out_items
        ]

        st.info(
            f"選択案件：{out_project['code']} / {out_project['name']}　"
            f"商品種類：{len(out_items):,}品"
        )

        shipping_pdf = create_project_document(
            "出荷指示書",
            out_project,
            out_company,
            instruction_items,
            include_barcode=True,
        )
        st.download_button(
            "📄 出荷指示書をダウンロード",
            data=shipping_pdf,
            file_name=(
                f"出荷指示書_{safe_filename(out_project['code'])}.pdf"
            ),
            mime="application/pdf",
            use_container_width=True,
        )
        st.caption(
            "出荷指示書を見てピッキングし、"
            "積み込み前に実際に載せる商品だけ読み取ります。"
        )
        st.write("---")

        if st.session_state.stock_out_registered:
            st.success(
                "今回の出庫登録は完了しています。"
                "納品書・受領書を保存してください。"
            )

            if st.session_state.stock_out_documents:
                documents = st.session_state.stock_out_documents
                st.markdown("### 📄 今回の納品書・受領書")
                doc_col1, doc_col2 = st.columns(2)

                with doc_col1:
                    st.download_button(
                        "📄 納品書をダウンロード",
                        data=documents["delivery"],
                        file_name=(
                            f"納品書_{safe_filename(out_project['code'])}.pdf"
                        ),
                        mime="application/pdf",
                        use_container_width=True,
                    )

                with doc_col2:
                    st.download_button(
                        "📄 受領書をダウンロード",
                        data=documents["receipt"],
                        file_name=(
                            f"受領書_{safe_filename(out_project['code'])}.pdf"
                        ),
                        mime="application/pdf",
                        use_container_width=True,
                    )

            if st.button(
                "🧹 次の出庫を開始",
                key="stock_out_start_next",
                use_container_width=True,
            ):
                clear_outbound_state()
                st.rerun()

        else:
            st.markdown("### 積み込み前チェック")
            st.caption(
                "案件に含まれる商品だけ追加できます。"
                "今回読み取った数量だけが納品書・受領書に載ります。"
            )

            st.text_input(
                "バーコード読み取り",
                key="stock_out_barcode_input",
                placeholder="ここを1回クリックしてからスキャン",
                on_change=add_outbound_barcode,
            )

            notice = st.session_state.stock_out_notice
            if notice:
                notice_type, notice_message = notice
                if notice_type == "error":
                    st.error(notice_message)
                else:
                    st.success(notice_message)

            scanned_codes = st.session_state.stock_out_scanned_codes
            scan_counter = Counter(
                split_unit_barcode(code)[0]
                for code in scanned_codes
            )
            checked_total = len(scanned_codes)

            metric1, metric2 = st.columns(2)
            metric1.metric("今回の読取", f"{checked_total:,}個")
            metric2.metric(
                "商品種類",
                f"{sum(1 for qty in scan_counter.values() if qty > 0):,}品",
            )

            scanned_rows = []
            for code, scanned_qty in scan_counter.items():
                if scanned_qty <= 0:
                    continue

                item = out_item_map.get(code)
                if item is None:
                    continue

                scanned_rows.append(
                    {
                        "商品コード": code,
                        "商品名": item["name"],
                        "今回読取": scanned_qty,
                        "案件残数": item["remaining_quantity"],
                    }
                )

            if scanned_rows:
                st.dataframe(
                    scanned_rows,
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info(
                    "まだ商品を読み取っていません。"
                    "積み込む商品を順番にスキャンしてください。"
                )

            cancel_col, reset_col = st.columns(2)

            with cancel_col:
                if st.button(
                    "↩️ 直前の読取を取り消す",
                    disabled=not scanned_codes,
                    use_container_width=True,
                ):
                    removed = (
                        st.session_state.stock_out_scanned_codes.pop()
                    )
                    st.session_state.stock_out_notice = (
                        "success",
                        f"取り消しました：{removed}",
                    )
                    st.rerun()

            with reset_col:
                if st.button(
                    "🗑 読取をすべてリセット",
                    disabled=not scanned_codes,
                    use_container_width=True,
                ):
                    clear_outbound_state()
                    st.rerun()

            st.write("---")
            can_confirm = checked_total > 0

            if can_confirm:
                st.success(
                    f"今回 {checked_total:,}個を出庫できます。"
                )
            else:
                st.warning(
                    "商品を1個以上読み取ると出庫確定できます。"
                )

            if st.button(
                f"✅ 読み取った {checked_total:,}個を出庫確定",
                type="primary",
                disabled=not can_confirm,
                use_container_width=True,
            ):
                shortages = []
                plan_overages = []
                confirmed_items = []

                for code, scanned_qty in scan_counter.items():
                    if scanned_qty <= 0:
                        continue

                    item = out_item_map[code]

                    current_stock = conn.execute(
                        """
                        SELECT COALESCE(SUM(qty), 0)
                        FROM stock_logs
                        WHERE project_id = ? AND item_id = ?
                        """,
                        (out_project_id, item["id"]),
                    ).fetchone()[0]

                    shipped_row = conn.execute(
                        """
                        SELECT COALESCE(
                            SUM(
                                CASE
                                    WHEN type = '出庫' AND qty < 0
                                    THEN -qty
                                    ELSE 0
                                END
                            ),
                            0
                        )
                        FROM stock_logs
                        WHERE project_id = ? AND item_id = ?
                        """,
                        (out_project_id, item["id"]),
                    ).fetchone()

                    latest_already_shipped = int(
                        shipped_row[0] or 0
                    )
                    latest_remaining = max(
                        int(item["required_quantity"])
                        - latest_already_shipped,
                        0,
                    )

                    if int(current_stock or 0) < scanned_qty:
                        shortages.append(
                            f"{code} {item['name']}："
                            f"在庫 {int(current_stock or 0):,} / "
                            f"今回 {scanned_qty:,}"
                        )

                    if scanned_qty > latest_remaining:
                        plan_overages.append(
                            f"{code} {item['name']}："
                            f"案件残数 {latest_remaining:,} / "
                            f"今回 {scanned_qty:,}"
                        )

                    confirmed_items.append(
                        {
                            "item": item,
                            "quantity": scanned_qty,
                        }
                    )

                if shortages:
                    st.error("在庫不足のため出庫できません。")
                    for message in shortages:
                        st.write(f"・{message}")

                elif plan_overages:
                    st.error(
                        "案件の残数量を超える商品があるため"
                        "出庫できません。"
                    )
                    for message in plan_overages:
                        st.write(f"・{message}")

                else:
                    document_items = [
                        {
                            "code": row["item"]["code"],
                            "name": row["item"]["name"],
                            "quantity": row["quantity"],
                        }
                        for row in confirmed_items
                    ]

                    try:
                        # 既存PDF関数をそのまま使用。
                        delivery_pdf = create_project_document(
                            "納品書",
                            out_project,
                            out_company,
                            document_items,
                            include_barcode=False,
                        )
                        receipt_pdf = create_project_document(
                            "受領書",
                            out_project,
                            out_company,
                            document_items,
                            include_barcode=False,
                        )

                        for row in confirmed_items:
                            item = row["item"]
                            quantity = int(row["quantity"])

                            conn.execute(
                                """
                                INSERT INTO stock_logs(
                                    project_id,
                                    item_id,
                                    qty,
                                    type,
                                    username
                                )
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    out_project_id,
                                    item["id"],
                                    -quantity,
                                    "出庫",
                                    st.session_state.username,
                                ),
                            )

                        conn.commit()
                        st.session_state.stock_out_documents = {
                            "delivery": delivery_pdf,
                            "receipt": receipt_pdf,
                        }
                        st.session_state.stock_out_registered = True
                        st.session_state.stock_out_notice = None
                        st.rerun()

                    except Exception as error:
                        rollback_connection(conn)
                        st.error(
                            f"出庫登録に失敗しました：{error}"
                        )
# === SHARK SIMPLE OUTBOUND 20260810 END ===

conn.close()
