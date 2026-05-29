import barcode
from barcode.writer import ImageWriter

code = "A001"

# Code128生成
barcode_class = barcode.get_barcode_class("code128")

barcode_obj = barcode_class(
    code,
    writer=ImageWriter()
)

# 保存
barcode_obj.save(f"barcodes/{code}")

print("バーコード作成完了")