"""主窗口外观：深色工作台 + 左侧目录，接近演示文稿阅读器。"""

APP_QSS = """
QMainWindow, QWidget#RootSplit, QWidget#ThumbPane, QWidget#PropPane {
    background: #3A3D42;
    color: #E6E8EB;
}
QMenu {
    background: #2F3237;
    color: #E6E8EB;
    border: 1px solid #1F2124;
}
QMenu::item:selected { background: #3E6FA8; }
QWidget#MainBar {
    background: #32353A;
    border: none;
    border-bottom: 1px solid #2A2C30;
}
QWidget#MainBar QToolButton {
    background: transparent;
    color: #E6E8EB;
    border: none;
    border-radius: 4px;
    padding: 4px;
}
QWidget#MainBar QToolButton:hover { background: #4A5160; }
QWidget#MainBar QToolButton:pressed { background: #2E3340; }
QWidget#MainBar QToolButton:checked { background: #4A5160; }
QWidget#MainBar QToolButton:disabled { color: #7A8088; }
QWidget#BarSep { background: #555B64; }
QLabel#ChromeLabel {
    color: #C5CAD1;
    font-size: 12px;
    min-width: 44px;
    padding: 0 6px;
}
QLabel#PaneTitle {
    color: #DDE1E6;
    font-size: 13px;
    font-weight: 600;
    padding: 10px 12px 6px 12px;
}
QListWidget#ThumbList {
    background: #2F3237;
    border: none;
    outline: none;
    color: #D0D4DA;
    padding: 4px 0 12px 0;
}
QListWidget#ThumbList::item {
    background: transparent;
    padding: 8px 6px 10px 6px;
    margin: 2px 10px;
    border-radius: 4px;
}
QListWidget#ThumbList::item:selected {
    background: #3A4658;
    border: 2px solid #4C9EEB;
}
QListWidget#ThumbList::item:hover:!selected {
    background: #3A3F46;
}
QSplitter::handle { background: #2A2C30; width: 1px; height: 1px; }
QStatusBar {
    background: #2C2F34;
    color: #B4B9C0;
    border-top: 1px solid #24262A;
}
QStatusBar QLabel { color: #B4B9C0; }
QGroupBox {
    border: 1px solid #4A4F56;
    border-radius: 4px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    color: #E6E8EB;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QComboBox, QDoubleSpinBox, QLineEdit, QListWidget {
    background: #2F3237;
    color: #E6E8EB;
    border: 1px solid #555B64;
    border-radius: 3px;
    padding: 2px 4px;
}
QPushButton {
    background: #4A5160;
    color: #E6E8EB;
    border: 1px solid #5B6370;
    border-radius: 3px;
    padding: 5px 10px;
}
QPushButton:hover { background: #5A6272; }
QScrollBar:vertical {
    background: #2F3237;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #5A616A;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QGraphicsView { background: #4B4F55; border: none; }
QWidget#ThumbPane {
    background: #2F3237;
    border-right: 1px solid #2A2C30;
}
QWidget#PropPane {
    background: #3A3D42;
    border-left: 1px solid #2A2C30;
}
"""
