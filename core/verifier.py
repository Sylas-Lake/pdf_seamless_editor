"""导出与验证层（对应方案第十章保真验证机制）。

1. 结构验证：重新打开保存文件，检查可解析性、页数、文本提取、加密状态；
2. 视觉回归：编辑前后按同一 DPI 栅格化，逐像素差异并排除编辑区域——
   非编辑区域原则上应接近零差异。
"""
import io

from .compat import fitz

import numpy as np

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def render_page_png(page, zoom: float = 1.5) -> bytes:
    """页面栅格化为 PNG 字节（保存前/后使用同一 zoom 保证可比）。"""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    to_png = getattr(pix, "tobytes", None) or getattr(pix, "getPNGData", None)
    return to_png("png")


def _png_to_array(png: bytes):
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return np.asarray(im)


def verify(saved_path: str, originals: dict, edit_regions: dict,
           zoom: float = 1.5, page_count: int = None) -> dict:
    """保存后验证。

    originals:     {page_index: png_bytes}（打开时的原始渲染）
    edit_regions:  {page_index: [rect...]}（编辑影响区域，PDF 坐标）
    返回报告 dict。
    """
    report = {"ok": True, "structure": [], "pages": []}
    checks = report["structure"]

    try:
        d = fitz.open(saved_path)
    except Exception as e:
        report["ok"] = False
        checks.append(f"重新打开失败：{e}")
        return report

    checks.append(f"文件可解析：共 {d.page_count} 页")
    if d.is_encrypted:
        checks.append("警告：文档处于加密状态")
    if page_count is not None and d.page_count != page_count:
        report["ok"] = False
        checks.append(f"错误：页数变化 {page_count} → {d.page_count}")
    else:
        checks.append("页数一致")

    for i in range(d.page_count):
        try:
            d[i].get_text("rawdict")
        except Exception as e:
            report["ok"] = False
            checks.append(f"第 {i + 1} 页文本层解析失败：{e}")

    checks.append("提示：建议使用 Chrome/PDFium、Adobe Acrobat 等第二渲染器交叉查看")

    for i in range(d.page_count):
        item = {"page": i, "note": "", "outside": 0.0, "inside": 0.0,
                "orig": None, "new": None, "diff": None}
        try:
            page = d[i]
            new_png = render_page_png(page, zoom)
            item["new"] = new_png
            orig_png = originals.get(i)
            if orig_png is None:
                item["note"] = "无原始渲染（可能为新增页）"
                report["pages"].append(item)
                continue
            a_new = _png_to_array(new_png)
            a_old = _png_to_array(orig_png)
            if a_new.shape != a_old.shape:
                item["note"] = "页面尺寸/分辨率变化，无法逐像素比较"
                report["ok"] = False
                report["pages"].append(item)
                continue
            diff = np.abs(a_new.astype(np.int16) - a_old.astype(np.int16)).sum(axis=2)
            changed = diff > 18
            h, w = changed.shape
            mask = np.zeros((h, w), dtype=bool)
            for r in (edit_regions or {}).get(i, []):
                x0 = max(0, int(r[0] * zoom))
                y0 = max(0, int(r[1] * zoom))
                x1 = min(w, int(np.ceil(r[2] * zoom)))
                y1 = min(h, int(np.ceil(r[3] * zoom)))
                if x1 > x0 and y1 > y0:
                    mask[y0:y1, x0:x1] = True
            total = changed.size
            outside = int((changed & ~mask).sum())
            inside = int((changed & mask).sum())
            item["outside"] = outside / total if total else 0.0
            item["inside"] = inside / total if total else 0.0
            if item["outside"] > 0.005:  # 非编辑区域差异 > 0.5%
                item["note"] = "非编辑区域存在差异，请检查"
                report["ok"] = False
            # 差异可视化：编辑区=橙，非编辑区=红
            vis = a_new.copy()
            vis[changed & ~mask] = (235, 64, 52)
            vis[changed & mask] = (250, 190, 40)
            if HAS_PIL:
                buf = io.BytesIO()
                Image.fromarray(vis).save(buf, format="PNG")
                item["diff"] = buf.getvalue()
            item["orig"] = orig_png
        except Exception as e:
            item["note"] = f"验证异常：{e}"
            report["ok"] = False
        report["pages"].append(item)
    d.close()
    return report
