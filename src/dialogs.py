from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk


class DetailsDialog(Adw.Dialog):
    def __init__(self, parent: Gtk.Window, log_buffer: Gtk.TextBuffer):
        super().__init__()
        self.set_title('Build Details')
        self.set_content_width(800)
        self.set_content_height(600)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        copy_button = Gtk.Button(label='Copy Log')
        copy_button.connect('clicked', lambda *_: self.copy_to_clipboard(log_buffer))
        header.pack_start(copy_button)

        textview = Gtk.TextView(buffer=log_buffer)
        textview.set_editable(False)
        textview.set_monospace(True)
        textview.set_left_margin(12)
        textview.set_right_margin(12)
        textview.set_top_margin(12)
        textview.set_bottom_margin(12)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(textview)
        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def copy_to_clipboard(self, buffer: Gtk.TextBuffer) -> None:
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False)
        self.get_clipboard().set(text)
