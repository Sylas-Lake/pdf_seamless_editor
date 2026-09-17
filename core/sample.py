"""示例 PDF 生成（用于演示与自测）。"""
import io
import os

try:
    import pymupdf as fitz
except ImportError:
    import fitz

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def _demo_image_bytes() -> bytes:
    im = Image.new("RGB", (360, 240), (236, 244, 252))
    dr = ImageDraw.Draw(im)
    dr.rectangle([16, 16, 344, 224], outline=(38, 84, 124), width=3)
    dr.ellipse([48, 60, 168, 180], fill=(52, 120, 246))
    dr.polygon([(200, 190), (260, 70), (320, 190)], fill=(245, 166, 35))
    dr.rectangle([60, 195, 300, 210], fill=(88, 170, 120))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def create_sample_pdf(path: str):
    doc = fitz.open()
    # ---------------- 第 1 页：文本 + 表格 ----------------
    p = doc.new_page()  # A4: 595 x 842
    x0, y = 72, 96
    p.insert_text((x0, y), "供货合同", fontname="china-s", fontsize=22)
    y += 34
    p.insert_text((x0, y), "合同编号：HT-2026-0917", fontname="china-s", fontsize=12)
    y += 24
    p.insert_text((x0, y), "签订日期：2026年09月17日", fontname="china-s", fontsize=12)
    y += 24
    p.insert_text((x0, y), "甲方（需方）：北京示例科技有限公司", fontname="china-s", fontsize=12)
    y += 24
    p.insert_text((x0, y), "乙方（供方）：上海无感软件有限公司", fontname="china-s", fontsize=12)
    y += 30
    p.insert_text((x0, y), "This agreement is made and entered into by both parties.",
                  fontname="helv", fontsize=11)
    y += 24
    p.insert_text((x0, y), "根据《中华人民共和国民法典》及相关法律法规，双方经协商一致，订立本合同。",
                  fontname="china-s", fontsize=12)
    y += 24
    p.insert_text((x0, y), "产品保质期为十二个月，自交付验收合格之日起计算。",
                  fontname="china-s", fontsize=12)
    # 简易表格（矢量线 + 单元格文本）
    ty = y + 30
    rows = [("品名", "数量", "单价（元）", "金额（元）"),
            ("智能传感器", "120", "258.00", "30,960.00"),
            ("数据采集模块", "40", "1,120.00", "44,800.00")]
    widths = [150, 70, 120, 120]
    rh = 26
    cx = x0
    rect = fitz.Rect(x0, ty, x0 + sum(widths), ty + rh * len(rows))
    p.draw_rect(rect, color=(0.2, 0.2, 0.2), width=0.8)
    for ci, wcol in enumerate(widths[:-1]):
        cx += wcol
        p.draw_line(fitz.Point(cx, ty), fitz.Point(cx, ty + rh * len(rows)),
                    color=(0.2, 0.2, 0.2), width=0.8)
    for ri in range(1, len(rows)):
        p.draw_line(fitz.Point(x0, ty + ri * rh), fitz.Point(x0 + sum(widths), ty + ri * rh),
                    color=(0.2, 0.2, 0.2), width=0.8)
    for ri, row in enumerate(rows):
        cx = x0
        for ci, cell in enumerate(row):
            p.insert_text((cx + 6, ty + ri * rh + 17), cell,
                          fontname="china-s", fontsize=10.5)
            cx += widths[ci]
    ty += rh * len(rows) + 34
    p.insert_text((x0, ty), "总计金额（大写）：柒万伍仟柒佰陆拾元整", fontname="china-s", fontsize=12)

    # ---------------- 第 2 页：图片 + 说明 ----------------
    p2 = doc.new_page()
    p2.insert_text((72, 96), "附件一：产品示意图", fontname="china-s", fontsize=16)
    if HAS_PIL:
        p2.insert_image(fitz.Rect(72, 120, 352, 300), stream=_demo_image_bytes())
    p2.insert_text((72, 330), "图1 智能传感器产品示意（可拖动、缩放、旋转）",
                  fontname="china-s", fontsize=11)
    p2.insert_text((72, 370), "备注：本示意图仅用于演示图片编辑能力。",
                  fontname="china-s", fontsize=12)
    p2.insert_text((72, 394), "验收标准：符合 GB/T 19001 质量管理体系要求。",
                  fontname="china-s", fontsize=12)

    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "示例文档.pdf")
    create_sample_pdf(out)
    print("生成示例：", out)
