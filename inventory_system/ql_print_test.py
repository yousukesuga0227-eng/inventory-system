from pathlib import Path

from PIL import Image

# Pillow 10以降で削除された Image.ANTIALIAS を brother_ql 用に復活させる
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from brother_ql.raster import BrotherQLRaster
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send


PRINTER_IP = "192.168.0.18"
MODEL = "QL-820NWB"

# ここに、さっきZIPから出したPNGラベル画像のパスを書く
IMAGE_PATH = r"E:\inventory_app\barcodes\project_items\test_label.png"

# DK-22205などの62mm幅ロール想定
LABEL_SIZE = "62"


def main():
    image_path = Path(IMAGE_PATH)

    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    img = Image.open(image_path).convert("RGB")

    qlr = BrotherQLRaster(MODEL)
    qlr.exception_on_warning = True

    instructions = convert(
        qlr=qlr,
        images=[img],
        label=LABEL_SIZE,
        rotate="auto",
        threshold=70.0,
        dither=False,
        compress=False,
        red=True,
        dpi_600=False,
        hq=True,
        cut=True,
    )

    send(
        instructions=instructions,
        printer_identifier=f"tcp://{PRINTER_IP}",
        backend_identifier="network",
        blocking=True,
    )

    print("印刷データを送信しました。")


if __name__ == "__main__":
    main()