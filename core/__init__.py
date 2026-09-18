# PDF 无感编辑器 · 核心引擎包
#
#   compat    —— PyMuPDF 导入兼容
#   types     —— PdfRect / PageState 等别名
#   models    —— GlyphNode / TextLine / TextBlock / PageModel
#   extractor —— 字符级逆向解析与文本框分组
#   fonts     —— 原嵌入子集 → 系统完整字体 → 内置替代
#   executor  —— Redaction 真删除 + 原位重建
#   commands  —— 页面快照命令与撤销栈
#   snapshot  —— 页面对象/内容流字节快照
#   fidelity  —— 保真等级（绿/黄/橙/红）
#   verifier  —— 保存后结构校验与视觉回归
#   geom      —— PDF 矩形与手柄缩放
#   sample    —— 示例文档生成
#   textbox   —— BoxBuffer 框内编辑缓冲
