# PDF 无感编辑器 · 核心引擎包
# 架构对应《PDF 无感编辑器核心技术方案》：
#   models    —— 核心数据模型（GlyphNode / TextLine / PageModel ...）
#   extractor —— 字符级逆向解析与视觉语义重建
#   fonts     —— 字体恢复与文字塑形支撑（原始子集→系统完整字体→内置替代）
#   executor  —— PDF 内容流局部重写（真删除 + 原位重建）
#   commands  —— 文档中间层统一命令（撤销/重做）
#   fidelity  —— 保真等级评估（绿/黄/橙/红）
#   verifier  —— 保存后结构校验与视觉回归
#   sample    —— 示例文档生成
