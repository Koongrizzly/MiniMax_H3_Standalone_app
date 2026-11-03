"""Shared GrizzlyMax Qt theme palette. No external dependencies beyond PySide6."""
from PySide6.QtGui import QColor, QPalette

BASE = {
    "#11151b": "window", "#e8eef6": "text", "#171f28": "tab", "#2e4051": "border",
    "#1d3040": "selected_tab", "#82e7ff": "highlight_text", "#263545": "border2",
    "#2a3948": "border2", "#75d8ff": "accent", "#0b0f14": "field",
    "#334556": "field_border", "#176c89": "selection", "#ffffff": "selection_text",
    "#18232e": "header", "#dce8f3": "header_text", "#126680": "progress",
    "#3c5267": "control_border", "#1b2732": "button", "#243544": "hover",
    "#66717a": "disabled", "#15191e": "disabled_bg", "#28b6df": "primary_edge",
    "#0d1319": "panel", "#7de3ff": "title", "#a8bac9": "muted",
    "#9ab8d8": "soft_text", "#0c1116": "scroll", "#395166": "scroll_handle",
}
THEMES = {
"default": dict(window="#11151b", text="#e8eef6", tab="#171f28", border="#2e4051", selected_tab="#1d3040", highlight_text="#82e7ff", border2="#263545", accent="#75d8ff", field="#0b0f14", field_border="#334556", selection="#176c89", selection_text="#ffffff", header="#18232e", header_text="#dce8f3", progress="#126680", control_border="#3c5267", button="#1b2732", hover="#243544", disabled="#66717a", disabled_bg="#15191e", primary_edge="#28b6df", panel="#0d1319", title="#7de3ff", muted="#a8bac9", soft_text="#9ab8d8", scroll="#0c1116", scroll_handle="#395166"),
"matrix": dict(window="#071007", text="#a5f4a1", tab="#0b1b0e", border="#21582b", selected_tab="#123a19", highlight_text="#6aff79", border2="#194823", accent="#54f56a", field="#020a04", field_border="#267d37", selection="#187637", selection_text="#ffffff", header="#102714", header_text="#b5ffc0", progress="#15a43b", control_border="#358a45", button="#102c16", hover="#1b4824", disabled="#50765a", disabled_bg="#0a140c", primary_edge="#62ff7a", panel="#061309", title="#70ff83", muted="#79a984", soft_text="#86d994", scroll="#030c05", scroll_handle="#2c8b40"),
"day": dict(window="#f0f0f0", text="#202020", tab="#e7e7e7", border="#b5b5b5", selected_tab="#ffffff", highlight_text="#174c82", border2="#c4c4c4", accent="#155c9a", field="#ffffff", field_border="#a8a8a8", selection="#337ab7", selection_text="#ffffff", header="#dedede", header_text="#202020", progress="#337ab7", control_border="#ababab", button="#e8e8e8", hover="#d5e5f5", disabled="#858585", disabled_bg="#e2e2e2", primary_edge="#1b679f", panel="#fafafa", title="#125b91", muted="#565656", soft_text="#575757", scroll="#e4e4e4", scroll_handle="#aaaaaa"),
"blue": dict(window="#23262b", text="#e4e9f1", tab="#292e35", border="#485360", selected_tab="#2d3f59", highlight_text="#a8c8ff", border2="#3a4652", accent="#2D68C4", field="#191d23", field_border="#4a5869", selection="#244f96", selection_text="#ffffff", header="#303842", header_text="#dce6f4", progress="#2D68C4", control_border="#526477", button="#323c48", hover="#355888", disabled="#8b969f", disabled_bg="#292d32", primary_edge="#4b83db", panel="#20252c", title="#8cb8ff", muted="#b1bfce", soft_text="#b6cfe9", scroll="#1d2228", scroll_handle="#60738a"),
"violet": dict(window="#25242b", text="#eee8f5", tab="#302c38", border="#61516e", selected_tab="#452465", highlight_text="#b77aff", border2="#494152", accent="#7F00FF", field="#1c1a22", field_border="#665378", selection="#6810c7", selection_text="#ffffff", header="#37303f", header_text="#eee0fa", progress="#7F00FF", control_border="#704d92", button="#393143", hover="#562a7f", disabled="#998ca1", disabled_bg="#2b2830", primary_edge="#7F00FF", panel="#24202a", title="#b77aff", muted="#c0b0cd", soft_text="#d4bde3", scroll="#211d26", scroll_handle="#79509d")
}
def theme_colors(name):
    return THEMES.get(name, THEMES["default"])

def recolor_stylesheet(sheet, name):
    if name == "default":
        return sheet
    colors = theme_colors(name)
    import re
    return re.sub(r"#[0-9a-fA-F]{6}", lambda m: colors.get(BASE.get(m.group().lower()), m.group()), sheet)

def build_palette(c):
    pal = QPalette()
    roles = {"Window":"window", "WindowText":"text", "Base":"field", "AlternateBase":"panel",
             "Text":"text", "Button":"button", "ButtonText":"text", "ToolTipBase":"header",
             "ToolTipText":"text", "Highlight":"selection", "HighlightedText":"selection_text",
             "Mid":"border", "Dark":"field_border", "Light":"header", "PlaceholderText":"muted",
             "BrightText":"accent", "Link":"accent"}
    for role, key in roles.items():
        pal.setColor(getattr(QPalette.ColorRole, role), QColor(c[key]))
    return pal
