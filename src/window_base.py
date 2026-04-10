from __future__ import annotations

import shlex
import shutil
from datetime import date
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from constants import APP_ID, APP_NAME, APP_VERSION
from dialogs import DetailsDialog
from paths import asset_path, bundled_tool
from runner import CommandRunner


class MainWindowBase(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title(APP_NAME)
        self.set_default_size(1200, 800)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
        .promo-banner {
            background-color: alpha(@accent_bg_color, 0.5);
            border: 3px solid alpha(@accent_bg_color, 0.1);
            border-radius: 16px;
        }

        .promo-banner .title-2 {
            color: @accent_fg_color;
        }

        .description-editor {
            border: 3px solid alpha(currentColor, 0.15);
            border-radius: 12px;
            background: alpha(currentColor, 0.03);
        }

        .description-editor textview {
            background: transparent;
        }

        .build-panel {
            border: 2px solid alpha(currentColor, 0.10);
            border-radius: 14px;
            background: alpha(currentColor, 0.03);
        }

        .mono-label {
            font-family: monospace;
        }

        .app-label {
            font-size: 1.08em;
        }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._register_app_icon()

        self.worker: CommandRunner | None = None
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        self.window_title = Adw.WindowTitle(
            title=APP_NAME,
            subtitle='Create professional audiobook files and sell them with sellmybook.app',
        )
        self.default_subtitle = 'Create professional audiobook files and sell them with sellmybook.app'

        self.header_progress = Gtk.ProgressBar()
        self.header_progress.set_hexpand(True)
        self.header_progress.set_show_text(False)
        self.header_progress.set_visible(False)
        self.header_progress.set_margin_top(4)

        self.editor_stack = Gtk.Stack()
        self.editor_stack.set_transition_duration(200)
        self.editor_stack.set_hhomogeneous(False)
        self.editor_stack.set_vhomogeneous(False)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_box.set_margin_top(8)
        title_box.set_margin_bottom(8)
        title_box.append(self.window_title)
        title_box.append(self.header_progress)
        header.set_title_widget(title_box)

        self.chapter_rows: list[dict] = []
        self.chapter_count: int = 0
        self._staging_dir: Path | None = None
        self._temp_cover_path: Path | None = None
        self._progress_pulse_id: int | None = None
        self._active_output_paths: tuple[Path, Path | None] | None = None
        self.editor_stack_frame: Gtk.Frame | None = None
        self.actions_section: Gtk.Widget | None = None
        self.promo_banners: list[Gtk.Widget] = []

        self.details_button = Gtk.Button(label='Details')
        self.details_button.set_visible(False)
        self.details_button.connect('clicked', lambda *_: self.show_details())
        header.pack_start(self.details_button)

        self.discard_project_button = Gtk.Button(label='Discard Project')
        self.discard_project_button.add_css_class('destructive-action')
        self.discard_project_button.set_visible(False)
        self.discard_project_button.connect('clicked', lambda *_: self.discard_project())
        header.pack_start(self.discard_project_button)

        self.about_button = Gtk.Button(label='About')
        self.about_button.connect('clicked', lambda *_: self.show_about())
        header.pack_start(self.about_button)

        self.build_button = Gtk.Button()
        self.build_button.add_css_class('suggested-action')
        self.build_button.connect('clicked', lambda *_: self.on_build_button_clicked())
        self.build_button.set_sensitive(False)

        self.build_button_spinner = Gtk.Spinner()
        self.build_button_spinner.set_spinning(False)
        self.build_button_spinner.set_visible(False)
        self.build_button_spinner.set_size_request(16, 16)
        self.build_button_spinner.set_valign(Gtk.Align.CENTER)
        self.build_button_label = Gtk.Label(label='Build Audiobook')
        self.build_button_label.set_valign(Gtk.Align.CENTER)
        build_button_content = Gtk.Box(spacing=8)
        build_button_content.set_valign(Gtk.Align.CENTER)
        build_button_content.append(self.build_button_spinner)
        build_button_content.append(self.build_button_label)
        self.build_button.set_child(build_button_content)
        self.build_button.set_margin_end(12)
        header.pack_end(self.build_button)

        self.preview_buffer = Gtk.TextBuffer()
        self.log_buffer = Gtk.TextBuffer()

        self.stack = Gtk.Stack()
        toolbar_view.set_content(self.stack)
        self.stack.add_named(self._build_welcome_page(), 'welcome')
        self.stack.add_named(self._build_editor_page(), 'editor')
        self.stack.set_visible_child_name('welcome')

        self.set_status('Import a folder to begin.')
        self.refresh_preview()
        self._load_cover_preview()

    def start_progress(self) -> None:
        self.header_progress.set_fraction(0.0)
        self.header_progress.set_visible(True)

    def stop_progress(self) -> None:
        if self._progress_pulse_id is not None:
            GLib.source_remove(self._progress_pulse_id)
            self._progress_pulse_id = None
        self.header_progress.set_visible(False)

    def set_progress(self, fraction: float) -> None:
        self.header_progress.set_fraction(min(1.0, max(0.0, fraction)))

    def _load_cover_preview(self) -> None:
        path = self.cover_file.get_text().strip()
        if not path or not Path(path).is_file():
            self.cover_picture.set_filename(None)
            self.cover_preview_button.set_sensitive(False)
            return

        self.cover_picture.set_filename(path)
        self.cover_preview_button.set_sensitive(True)

    def _register_app_icon(self) -> None:
        icon_path = asset_path(f'{APP_ID}.png')
        if not icon_path.exists():
            return

        icon_theme = Gtk.IconTheme.get_for_display(self.get_display())
        icon_dir = str(icon_path.parent)
        if icon_dir not in icon_theme.get_search_path():
            icon_theme.add_search_path(icon_dir)

    def show_about(self, *_args) -> None:
        dialog = Adw.AboutDialog.new()
        dialog.set_application_name(APP_NAME)
        dialog.set_application_icon(APP_ID)
        dialog.set_version(APP_VERSION)
        dialog.set_developer_name('Evolve Systems Distribution Pty Ltd')
        dialog.set_website('https://sellmybook.app')
        dialog.set_issue_url('https://sellmybook.app/page/contact-us')
        dialog.set_comments(
            'Create professional .m4b and .mp3 audiobook files.\n\n'
            'SMB Audiobook Creator is licensed under the Apache License 2.0.\n\n'
            'This software uses third-party components including GTK4, Libadwaita, '
            'PyGObject, the ffmpeg and ffprobe binaries from an LGPL-configured '
            'FFmpeg build, libmp3lame, and tone.'
        )
        dialog.add_legal_section(
            'SMB Audiobook Creator',
            'Licensed under the Apache License 2.0.',
            Gtk.License.APACHE_2_0,
            None,
        )
        dialog.add_legal_section(
            'FFmpeg',
            'This product includes and invokes the ffmpeg and ffprobe binaries from '
            'an LGPL-configured build of the FFmpeg project.',
            Gtk.License.CUSTOM,
            None,
        )
        dialog.add_legal_section(
            'libmp3lame',
            'This product uses libmp3lame as a separate third-party library.',
            Gtk.License.CUSTOM,
            None,
        )
        dialog.add_legal_section(
            'tone',
            'This product includes tone for metadata tagging.',
            Gtk.License.CUSTOM,
            None,
        )
        dialog.add_credit_section('Contributors', ['James North'])
        dialog.set_copyright('Copyright Evolve Systems Distribution Pty Ltd')
        dialog.set_debug_info(
            'Third-party components used by this app include GTK4, Libadwaita, '
            'PyGObject, the ffmpeg and ffprobe binaries from an LGPL-configured '
            'FFmpeg build, libmp3lame, and tone. '
            'See THIRD_PARTY_NOTICES.md for notices and license details.'
        )
        dialog.add_link('GTK4', 'https://www.gtk.org')
        dialog.add_link('Libadwaita', 'https://gnome.pages.gitlab.gnome.org/libadwaita/')
        dialog.add_link('PyGObject', 'https://pygobject.gnome.org')
        dialog.add_link('FFmpeg', 'https://ffmpeg.org')
        dialog.add_link('libmp3lame', 'https://www.mp3dev.org')
        dialog.add_link('tone', 'https://github.com/sandreas/tone')
        dialog.add_link('James North', 'https://jamesnorth.net')
        dialog.present(self)

    def _label(
        self,
        text: str = '',
        *,
        dim: bool = False,
        bold: bool = False,
        wrap: bool = True,
        xalign: float = 0,
        justify: Gtk.Justification = Gtk.Justification.LEFT,
    ) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
        lbl.set_justify(justify)
        lbl.set_margin_start(8)
        lbl.set_margin_end(8)
        lbl.add_css_class('app-label')
        if dim:
            lbl.add_css_class('dim-label')
        if bold:
            lbl.add_css_class('heading')
        return lbl

    def open_sellmybook(self, *_args) -> None:
        Gio.AppInfo.launch_default_for_uri('https://sellmybook.app/page/checkbook', None)

    def show_repo_image_fullscreen(self, *_args) -> None:
        image_path = asset_path('gmb-page.png')
        if not image_path.exists():
            return

        dialog = Adw.Dialog()
        dialog.set_title('Sales page on GetMyBook.store for Book Publishing Secrets For Entrepreneurs')
        dialog.set_content_width(1100)
        dialog.set_content_height(800)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        picture = Gtk.Picture.new_for_filename(str(image_path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(picture)
        scroller.set_vexpand(True)

        claim_button = Gtk.Button(label='Claim Your Book')
        claim_button.add_css_class('suggested-action')
        claim_button.add_css_class('pill')
        claim_button.set_halign(Gtk.Align.CENTER)
        claim_button.set_margin_top(12)
        claim_button.set_margin_bottom(12)
        claim_button.connect('clicked', self.open_sellmybook)

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        content_box.append(scroller)
        content_box.append(claim_button)
        toolbar_view.set_content(content_box)
        dialog.set_child(toolbar_view)
        dialog.present(self)

    def dismiss_promo_banner(self, *_args) -> None:
        for banner in self.promo_banners:
            banner.set_visible(False)

    def show_promo_banner(self) -> None:
        for banner in self.promo_banners:
            banner.set_visible(True)

    def _promo_banner(self) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class('promo-banner')
        overlay.set_child(outer)

        dismiss_button = Gtk.Button(icon_name='window-close-symbolic')
        dismiss_button.set_valign(Gtk.Align.START)
        dismiss_button.set_halign(Gtk.Align.END)
        dismiss_button.set_margin_top(12)
        dismiss_button.set_margin_end(12)
        dismiss_button.add_css_class('flat')
        dismiss_button.connect('clicked', self.dismiss_promo_banner)
        overlay.add_overlay(dismiss_button)

        content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=20,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        right.set_valign(Gtk.Align.CENTER)

        title = self._label('We Built a New Bookstore That Puts Authors First... Your Book Might Already Be There!')
        title.add_css_class('title-2')

        body = self._label("GetMyBook.Store is a bookstore built for authors. You get a single, clean page that links to every retailer and every format of your book and nothing else. No competing ads and no algorithm pushing readers toward someone else's book. You control what's on it, and you can share it anywhere. We've already started adding titles. Yours may be one of them!")
        body2 = self._label('This is totally free. Claiming your book takes less than sixty seconds.')

        cta = Gtk.Button(label='Claim Your Book', margin_top=12)
        cta.add_css_class('pill')
        cta.add_css_class('suggested-action')
        cta.set_halign(Gtk.Align.CENTER)
        cta.connect('clicked', self.open_sellmybook)

        right.append(title)
        right.append(body)
        right.append(body2)
        right.append(cta)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        image_path = asset_path('gmb-page.png')
        self.promo_picture = Gtk.Picture()
        self.promo_picture.set_size_request(260, 180)
        self.promo_picture.set_can_shrink(True)
        self.promo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        if image_path.exists():
            self.promo_picture.set_filename(str(image_path))

        promo_button = Gtk.Button()
        promo_button.set_child(self.promo_picture)
        promo_button.connect('clicked', self.show_repo_image_fullscreen)
        left.append(promo_button)

        content.append(left)
        content.append(right)
        outer.append(content)
        self.promo_banners.append(overlay)
        return overlay

    def show_cover_fullscreen(self, *_args) -> None:
        path = self.cover_file.get_text().strip()
        if not path or not Path(path).is_file():
            return

        dialog = Adw.Dialog()
        dialog.set_title('Cover Preview')
        dialog.set_content_width(900)
        dialog.set_content_height(900)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        picture = Gtk.Picture.new_for_filename(path)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        box.append(picture)

        toolbar_view.set_content(box)
        dialog.set_child(toolbar_view)
        dialog.present(self)

    def on_build_button_clicked(self) -> None:
        if self.worker is not None:
            self.cancel_build()
            return
        self.start_build()

    def set_build_running(self, running: bool) -> None:
        if self.editor_stack_frame is not None:
            self.editor_stack_frame.set_sensitive(not running)
        if self.actions_section is not None:
            self.actions_section.set_sensitive(not running)
        self.details_button.set_sensitive(not running)
        self.discard_project_button.set_sensitive(not running)
        self.build_button.set_sensitive(self.input_dir.get_text().strip() != '')
        self.action_build_button.set_sensitive(self.input_dir.get_text().strip() != '')
        self.build_button_spinner.set_spinning(running)
        self.build_button_spinner.set_visible(running)
        self.action_build_button_spinner.set_spinning(running)
        self.action_build_button_spinner.set_visible(running)
        self.build_button_label.set_text('Cancel' if running else 'Build Audiobook')
        self.action_build_button_label.set_text('Cancel' if running else 'Build Audiobook')
        if running:
            self.build_button.set_sensitive(True)
            self.action_build_button.set_sensitive(True)

    def _set_project_loaded(self, loaded: bool) -> None:
        self.build_button.set_sensitive(loaded)
        self.action_build_button.set_sensitive(loaded)
        self.discard_project_button.set_visible(loaded)
        self.discard_project_button.set_sensitive(loaded and self.worker is None)

    def discard_project(self) -> None:
        if self.worker is not None:
            return

        self.chapter_rows.clear()
        self._rebuild_chapters_ui()
        self.input_dir.set_text('')
        self.cover_file.set_text('')
        self._load_cover_preview()
        self.title_entry.set_text('')
        self.artist_entry.set_text('')
        self.writer_entry.set_text('')
        self.year_entry.set_text(str(date.today().year))
        self.narrator_entry.set_text('')
        self.description_buffer.set_text('')
        self.create_mp3_switch.set_active(False)
        self.log_buffer.set_text('')
        self.set_status_details('')
        self.details_button.set_visible(False)
        self._active_output_paths = None
        self.stack.set_visible_child_name('welcome')
        self._set_project_loaded(False)
        self.refresh_preview()
        self.set_status('Import a folder to begin.')

    def cancel_build(self) -> None:
        if self.worker is None:
            return
        self.worker.cancel()
        self.set_status('Cancelling build…')

    def scroll_to_editor_top(self) -> bool:
        adjustment = self.editor_scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_lower())
        return False

    def _card(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title_label = self._label(title, bold=True)
        title_label.add_css_class('title-3')
        frame = Gtk.Frame()
        frame.add_css_class('card')
        frame.set_child(child)
        box.append(title_label)
        box.append(frame)
        return box

    def _readonly_chooser_row(self, label: str, entry: Gtk.Entry, button_label: str, callback) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        row.append(self._label(label, bold=True))

        inner = Gtk.Box(spacing=8)
        inner.append(entry)
        button = Gtk.Button(label=button_label)
        button.connect('clicked', callback)
        inner.append(button)
        row.append(inner)
        return row

    def _simple_entry(self, initial: str = '') -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.connect('changed', lambda *_: self.refresh_preview())
        return entry

    def shell_join(self, parts: list[str]) -> str:
        return ' '.join(shlex.quote(str(p)) for p in parts if p is not None)

    def append_log(self, text: str) -> bool:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, text + '\n')
        return False

    def set_status(self, text: str) -> None:
        self.window_title.set_subtitle(text or self.default_subtitle)

    def set_status_details(self, text: str = '') -> None:
        return

    def show_toast(
        self,
        text: str,
        button_label: str | None = None,
        callback=None,
        *,
        persistent: bool = False,
        high_priority: bool = False,
    ) -> None:
        toast = Adw.Toast(title=text)
        toast.set_timeout(0 if persistent else 5)
        if high_priority:
            toast.set_priority(Adw.ToastPriority.HIGH)

        if button_label and callback:
            toast.set_button_label(button_label)
            toast.connect('button-clicked', lambda *_: callback())

        self.toast_overlay.add_toast(toast)

    def show_details(self) -> None:
        dialog = DetailsDialog(self, self.log_buffer)
        dialog.present(self)

    def choose_folder(self, *_args) -> None:
        dialog = Gtk.FileDialog(title='Import from folder')
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            file = dialog.select_folder_finish(result)
        except Exception:
            return

        path = file.get_path()
        if path:
            self.input_dir.set_text(path)
            self.populate_from_folder(silent=True)
            self.stack.set_visible_child_name('editor')
            GLib.idle_add(self.scroll_to_editor_top)
            self._set_project_loaded(True)
            self.refresh_preview()

    def choose_export_folder(self, *_args) -> None:
        dialog = Gtk.FileDialog(title='Select export folder')
        dialog.select_folder(self, None, self._on_export_folder_selected)

    def _on_export_folder_selected(self, dialog, result) -> None:
        try:
            file = dialog.select_folder_finish(result)
        except Exception:
            return

        path = file.get_path()
        if path:
            self.input_dir.set_text(path)
            self._set_project_loaded(True)
            self.refresh_preview()

    def choose_cover(self, *_args) -> None:
        file_filter = Gtk.FileFilter()
        file_filter.add_mime_type('image/jpeg')
        file_filter.add_mime_type('image/png')
        file_filter.set_name('Images')

        dialog = Gtk.FileDialog(title='Choose cover image')
        dialog.set_default_filter(file_filter)
        dialog.open(self, None, self._on_cover_selected)

    def _on_cover_selected(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except Exception:
            return

        path = file.get_path()
        if path:
            self.cover_file.set_text(path)
            self._load_cover_preview()
            self.refresh_preview()

    def open_input_folder(self) -> None:
        folder = self.input_dir.get_text().strip()
        if not folder:
            self.show_toast('Choose an export folder first.')
            return
        uri = Gio.File.new_for_path(folder).get_uri()
        Gio.AppInfo.launch_default_for_uri(uri, None)

    def _cleanup_temp_cover(self) -> None:
        if self._temp_cover_path and self._temp_cover_path.exists():
            try:
                self._temp_cover_path.unlink()
            except Exception:
                pass
        self._temp_cover_path = None

    def validate(self) -> str | None:
        if self.worker is not None:
            return 'A build is already running.'
        folder = self.input_dir.get_text().strip()
        if not folder:
            return 'Choose an export folder.'
        if not Path(folder).is_dir():
            return 'Audiobook folder does not exist.'
        if not self.title_entry.get_text().strip():
            return 'Enter a title.'
        year = self.year_entry.get_text().strip()
        if year and not year.isdigit():
            return 'Year must be numeric.'
        cover = self.cover_file.get_text().strip()
        if cover and not Path(cover).is_file():
            return 'Cover image file does not exist.'
        return None

    def _description_text(self) -> str:
        start = self.description_buffer.get_start_iter()
        end = self.description_buffer.get_end_iter()
        return self.description_buffer.get_text(start, end, False).strip()

    def open_output_folder(self) -> None:
        m4b_path, _mp3_path = self.get_output_paths()
        if m4b_path is None:
            return
        folder = m4b_path.parent
        uri = Gio.File.new_for_path(str(folder)).get_uri()
        Gio.AppInfo.launch_default_for_uri(uri, None)

    def check_tools(self):
        missing = []
        for tool in ['ffmpeg', 'ffprobe', 'tone']:
            resolved = bundled_tool(tool)
            if '/' in resolved:
                ok = Path(resolved).exists()
            else:
                ok = shutil.which(resolved) is not None
            if not ok:
                missing.append(tool)

        if missing:
            self.show_toast('Missing tools: ' + ', '.join(missing))
            return False
        return True

    def start_build(self) -> None:
        if not self.check_tools():
            return
        problem = self.validate()
        if problem:
            self.show_toast(problem)
            return

        output_paths = self.get_output_paths()
        if output_paths[0] is None:
            self.show_toast('Choose an export folder.')
            return
        self._active_output_paths = output_paths

        self.log_buffer.set_text('')
        self.set_status_details('')
        self.show_promo_banner()
        self.start_progress()
        self.set_status('Preparing files…')
        self.set_build_running(True)

        self.worker = CommandRunner(self)
        self.worker.start()

    def _cleanup_staging_dir(self) -> None:
        if self._staging_dir and self._staging_dir.exists():
            shutil.rmtree(self._staging_dir, ignore_errors=True)
        self._staging_dir = None

    def finish_build(self, ok: bool, message: str) -> bool:
        active_output_paths = self._active_output_paths
        self.worker = None
        self._active_output_paths = None
        self.stop_progress()
        self.set_build_running(False)
        self.set_status('')
        self.details_button.set_visible(True)
        self._cleanup_staging_dir()

        if ok:
            if active_output_paths is not None:
                m4b_path, mp3_path = active_output_paths
            else:
                m4b_path, mp3_path = self.get_output_paths()
            exported = []
            if m4b_path is not None:
                exported.append(str(m4b_path))
            if mp3_path is not None:
                exported.append(str(mp3_path))
            toast_message = message
            if exported:
                toast_message = message + '\n' + '\n'.join(exported)
            self.set_status_details('')
            self.show_toast(toast_message, 'Show files', self.open_output_folder, persistent=True, high_priority=True)
        else:
            self.set_status_details('')
            self.show_toast(message, persistent=True, high_priority=True)

        return False
