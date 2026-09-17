"""页面级状态快照（真正的原版恢复）。

撤销/重做不再通过"重新插入文本"模拟，而是直接保存并恢复页面
对象与内容流的原始字节——回到编辑前的真实 PDF 状态。

快照内容：
  - 页面对象（page dict）原始源码（含 /Contents 引用与内联 /Resources）
  - 全部内容流 xref 及其字节
  - 独立 /Resources 对象的原始源码（若为引用形式）
"""
import re

try:
    import pymupdf as fitz
except ImportError:
    import fitz

_RES_RE = re.compile(r"/Resources\s+(\d+)\s+0\s+R")


def _xref_source(doc, xref) -> str:
    """读取对象源码（兼容 raw / compressed 参数名）。"""
    try:
        return doc.xref_object(xref, raw=True)
    except TypeError:
        try:
            return doc.xref_object(xref, compressed=False)
        except TypeError:
            return doc.xref_object(xref)


def capture_page_state(doc, page) -> dict:
    """捕获页面当前完整状态。"""
    page_xref = page.xref
    page_obj = _xref_source(doc, page_xref)
    try:
        cont_xrefs = list(page.get_contents())
    except Exception:
        cont_xrefs = []
    contents = []
    for x in cont_xrefs:
        try:
            contents.append((x, doc.xref_stream(x)))
        except Exception:
            contents.append((x, None))

    res_xref = None
    res_obj = None
    m = _RES_RE.search(page_obj or "")
    if m:
        res_xref = int(m.group(1))
        try:
            res_obj = _xref_source(doc, res_xref)
        except Exception:
            res_xref, res_obj = None, None

    return {
        "page_xref": page_xref,
        "page_obj": page_obj,
        "contents": contents,
        "res_xref": res_xref,
        "res_obj": res_obj,
    }


def restore_page_state(doc, page, state: dict, page_index: int = None):
    """把页面恢复到快照状态（字节级）。"""
    if not state:
        return
    # 1) 恢复内容流字节
    for x, data in state["contents"]:
        if data is None:
            continue
        try:
            if doc.xref_is_stream(x):
                doc.update_stream(x, data)
            else:
                doc.update_object(x, "<< /Length %d >> stream\n%s\nendstream"
                                  % (len(data), data.decode("latin-1", "ignore")))
        except Exception:
            pass
    # 2) 恢复独立 /Resources 对象
    if state["res_xref"] and state["res_obj"]:
        try:
            doc.update_object(state["res_xref"], state["res_obj"])
        except Exception:
            pass
    # 3) 恢复页面对象（/Contents 指回原始内容流，内联资源复原）
    try:
        doc.update_object(state["page_xref"], state["page_obj"])
    except Exception:
        pass
    # 4) 使缓存失效
    try:
        doc.reload_page(page)
    except Exception:
        pass


def page_state_equal(doc, page, state: dict) -> bool:
    """当前页面状态与快照是否一致（字节级比较，调试/自测用）。"""
    if not state:
        return True
    try:
        if _xref_source(doc, page.xref) != state["page_obj"]:
            return False
        cur = set(page.get_contents())
        for x, data in state["contents"]:
            if x not in cur:
                return False
            if data is not None and doc.xref_stream(x) != data:
                return False
        return True
    except Exception:
        return False
