from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio

from constants import APP_ID
from window import MainWindow


class M4BApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        Adw.init()
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action('app.quit', ['<Primary>q'])

    def _on_quit(self, *_args) -> None:
        self.quit()

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = MainWindow(self)
        window.present()

def run() -> int:
    return M4BApplication().run()
