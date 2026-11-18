"""Invoice PDF of the fake portal: one page, PDF-standard CJK font STSong-Light (UCS-2), with a ToUnicode map so
the text layer can be extracted (same minimal writer as Demo A's generator)."""
from __future__ import annotations

from .db import Order


def _hex(text: str) -> str:
    return "<" + text.encode("utf-16-be").hex().upper() + ">"


def _to_unicode_cmap() -> bytes:
    high = [h for h in range(256) if not 0xD8 <= h <= 0xDF]
    parts = ["/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def /CMapType 2 def",
             "1 begincodespacerange <0000> <FFFF> endcodespacerange"]
    for i in range(0, len(high), 100):
        chunk = high[i:i + 100]
        parts.append(f"{len(chunk)} beginbfrange")
        parts += [f"<{h:02X}00> <{h:02X}FF> <{h:02X}00>" for h in chunk]
        parts.append("endbfrange")
    parts.append("endcmap CMapName currentdict /CMap defineresource pop end end")
    return "\n".join(parts).encode()


def _pdf(ops: list[str]) -> bytes:
    stream = "\n".join(ops).encode()
    cmap = _to_unicode_cmap()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H "
        b"/DescendantFonts [6 0 R] /ToUnicode 8 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo "
        b"<< /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /FontDescriptor 7 0 R /DW 1000 /W [1 95 500] >>",
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880] "
        b"/ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 93 >>",
        b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def invoice_pdf(order: Order) -> bytes:
    """Machine-readable lines (发票号码/销售单号/价税合计) are what download_invoice parses."""
    assert order.invoice is not None
    ops: list[str] = []

    def text(x: float, y: float, s: str, size: float = 10) -> None:
        ops.append(f"BT /F1 {size} Tf {x} {y} Td {_hex(s)} Tj ET")

    text(180, 790, "增值税发票（演示样张，非真实发票）", 15)
    text(40, 750, f"发票号码：{order.invoice.invoice_no}")
    text(330, 750, f"开票日期：{order.invoice.issue_date}")
    text(40, 730, f"销售单号：{order.order_no}")
    text(330, 730, f"客户订单号：{order.cust_po}")
    text(40, 710, f"购买方：{order.customer.name}")
    text(40, 690, "销售方：瑞丰机电批发（虚构）")
    y = 660
    for x, h in zip([40, 110, 300, 350, 410, 490], ["物料编码", "名称规格", "单位", "数量", "含税单价", "金额"]):
        text(x, y, h, 9)
    for line in order.lines:
        y -= 18
        for x, v in zip([40, 110, 300, 350, 410, 490], [line.sku, f"{line.name} {line.spec}", line.unit,
                                                        f"{line.qty:g}", f"{line.unit_price:.2f}", f"{line.amount:.2f}"]):
            text(x, y, v, 9)
    text(40, y - 30, f"价税合计：{order.invoice.amount:.2f}", 11)
    text(40, 60, "演示数据均为虚构。", 7)
    return _pdf(ops)
