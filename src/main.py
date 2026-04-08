#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import tempfile
from datetime import date
from pathlib import Path

import gi
import os
import sys

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = 'app.sellmybook.smbaudiobookcreator'
APP_NAME = 'SMB Audiobook Creator'
APP_VERSION = '2.0.0'
SUPPORTED_AUDIO_EXTS = (
    '.mp3', '.m4a', '.m4b', '.aac', '.flac', '.ogg', '.oga', '.wav', '.wma', '.mp4', '.alac'
)

if getattr(sys, "frozen", False) and sys.platform == "darwin":
    base = Path(sys.executable).resolve().parent.parent / "Resources"

    os.environ["GI_TYPELIB_PATH"] = str(base / "lib" / "girepository-1.0")
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(base / "share" / "glib-2.0" / "schemas")
    os.environ["GTK_PATH"] = str(base / "lib")
    os.environ["DYLD_LIBRARY_PATH"] = str(base / "lib")
    
    # Ensure bundled helper binaries are visible inside the app bundle.
    bundled_bin = str(base / "bin")
    if bundled_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{bundled_bin}:{os.environ.get('PATH', '')}"

def asset_path(name: str) -> Path:
    if getattr(sys, 'frozen', False):
        # 1. Check PyInstaller's temp root (_MEIPASS)
        if hasattr(sys, '_MEIPASS'):
            base = Path(sys._MEIPASS)
            # Try both root and data/
            for candidate in [base / 'data' / name, base / name]:
                if candidate.exists():
                    return candidate

        # 2. Check macOS app bundle Resources/data
        # sys.executable is Contents/MacOS/App
        res_base = Path(sys.executable).resolve().parent.parent / 'Resources'
        for candidate in [res_base / 'data' / name, res_base / name]:
            if candidate.exists():
                return candidate

        return res_base / 'data' / name # Final fallback

    source_root = Path(__file__).resolve().parent.parent
    candidates = [
        # Flatpak install layout
        Path('/app/share/smb-ab-creator/data') / name,
        Path('/app/share/smb-ab-creator') / name,
        # In dev, src/main.py is at project_root/src/main.py
        # so data is at project_root/data
        source_root / 'data' / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]

def bundled_tool(name: str) -> str:
    # For packaged macOS apps, prefer bundled tools inside the app bundle.
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
        candidates = [
            base / 'bin' / name,
            base / '_internal' / 'bin' / name,
            base.parent / 'Resources' / 'bin' / name,
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)

    # For Flatpak/Linux dev/macOS dev, just use PATH.
    found = shutil.which(name)
    return found or name

class CommandRunner(threading.Thread):
    def __init__(
        self,
        app: 'MainWindow',
    ):
        super().__init__(daemon=True)
        self.app = app
        self.cancelled = threading.Event()
        self.process: subprocess.Popen[str] | None = None

    def run(self) -> None:
        try:
            self._set_progress(0.0)
            self._check_cancelled()
            self._set_stage('Preparing files…')
            build_input_dir, staged_chapters = self.app.stage_support_files(self._set_progress)

            self._check_cancelled()
            self._build_audiobook(build_input_dir, staged_chapters)

            tone_cmd = self.app.build_tone_command()
            if tone_cmd:
                self._check_cancelled()
                self._set_stage('Applying metadata…')
                self._run_command(tone_cmd)
                self._set_progress(0.95)

            mp3_cmd = self.app.build_mp3_command()
            if mp3_cmd:
                self._check_cancelled()
                self._set_stage('Creating MP3 copy…')
                self._run_command(mp3_cmd)

            self._set_progress(1.0)
            self._finish(True, 'Build finished')
        except Exception as exc:  # pragma: no cover
            self._finish(False, str(exc))

    def _run_command(self, cmd: list[str]) -> None:
        self._check_cancelled()
        self._append_log(self.app.shell_join(cmd))
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.process = process
        assert process.stdout is not None
        
        for line in process.stdout:
            if self.cancelled.is_set():
                process.terminate()
            line_text = line.rstrip()
            self._append_log(line_text)

        code = process.wait()
        self.process = None
        self._check_cancelled()
        if code != 0:
            raise RuntimeError(f'Command failed with exit code {code}: {self.app.shell_join(cmd)}')

    def _build_audiobook(self, build_input_dir: Path, staged_chapters: list[dict]) -> None:
        m4b_output, _mp3_output = self.app.get_output_paths()
        assert m4b_output is not None

        transcode_dir = build_input_dir / '_aac'
        transcode_dir.mkdir(exist_ok=True)

        self._set_stage('Transcoding chapters…')
        transcoded_files: list[Path] = []
        chapter_entries: list[dict[str, int | str]] = []
        chapter_start_ms = 0
        total = len(staged_chapters)

        for idx, chapter in enumerate(staged_chapters, start=1):
            self._check_cancelled()
            output_path = transcode_dir / f'{idx:03d}.m4a'
            cmd = [
                bundled_tool('ffmpeg'),
                '-y',
                '-i', str(chapter['staged_path']),
                '-map', '0:a:0',
                '-vn',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-metadata', f'title={chapter["title"]}',
                str(output_path),
            ]
            self._run_command(cmd)
            duration = self.app.probe_duration(output_path)
            duration_ms = max(1, int(round(duration * 1000)))
            chapter_entries.append({
                'start': chapter_start_ms,
                'end': chapter_start_ms + duration_ms,
                'title': str(chapter['title']),
            })
            chapter_start_ms += duration_ms
            transcoded_files.append(output_path)
            self._set_progress(0.1 + (idx / total) * 0.55)

        self._check_cancelled()
        self._set_stage('Merging audiobook…')
        concat_file = build_input_dir / 'concat.txt'
        concat_file.write_text(self.app.build_concat_file(transcoded_files), encoding='utf-8')
        merged_audio = build_input_dir / 'merged.m4a'
        self._run_command([
            bundled_tool('ffmpeg'),
            '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            str(merged_audio),
        ])
        self._set_progress(0.72)

        self._check_cancelled()
        self._set_stage('Writing metadata…')
        metadata_file = build_input_dir / 'metadata.ffmeta'
        metadata_file.write_text(self.app.build_ffmetadata(chapter_entries), encoding='utf-8')
        mux_cmd = self.app.build_final_mux_command(build_input_dir, merged_audio, metadata_file, m4b_output)
        self._run_command(mux_cmd)
        self._set_progress(0.9)

    def cancel(self) -> None:
        self.cancelled.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def _check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise RuntimeError('Build cancelled.')

    def _set_progress(self, fraction: float) -> None:
        GLib.idle_add(self.app.set_progress, fraction)

    def _append_log(self, text: str) -> None:
        GLib.idle_add(self.app.append_log, text)

    def _set_stage(self, text: str) -> None:
        GLib.idle_add(self.app.set_status, text)

    def _finish(self, ok: bool, text: str) -> None:
        GLib.idle_add(self.app.finish_build, ok, text)

class DetailsDialog(Adw.Dialog):
    def __init__(self, parent: Gtk.Window, log_buffer: Gtk.TextBuffer):
        super().__init__()
        self.set_title("Build Details")
        self.set_content_width(800)
        self.set_content_height(600)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        copy_button = Gtk.Button(label="Copy Log")
        copy_button.connect("clicked", lambda *_: self.copy_to_clipboard(log_buffer))
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

    def copy_to_clipboard(self, buffer: Gtk.TextBuffer):
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False)
        self.get_clipboard().set(text)

class MainWindow(Adw.ApplicationWindow):
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
            subtitle='Create professional audiobook files and sell them with sellmybook.app'
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
        val = min(1.0, max(0.0, fraction))
        self.header_progress.set_fraction(val)

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
        dialog.add_credit_section(
            'Contributors',
            ['James North'],
        )
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

        # Image
        picture = Gtk.Picture.new_for_filename(str(image_path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(picture)
        scroller.set_vexpand(True)

        # CTA Button
        claim_button = Gtk.Button(label='Claim Your Book')
        claim_button.add_css_class('suggested-action')
        claim_button.add_css_class('pill')
        claim_button.set_halign(Gtk.Align.CENTER)
        claim_button.set_margin_top(12)
        claim_button.set_margin_bottom(12)
        claim_button.connect('clicked', self.open_sellmybook)

        # Layout container
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

    def _build_welcome_page(self) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        box.set_size_request(640, -1)

        title = self._label('', xalign=0.5)
        title.set_markup('<span size="xx-large" weight="bold">Create a professional .m4b audiobook file super quickly!</span>')
        title.set_wrap(True)

        step1 = self._label('Step 1. Open a folder once to import chapter files.')
        step2 = self._label('Step 2. Edit the metadata and reorganise your chapters.')
        step3 = self._label('Step 3. Click "Build Audiobook" and wait.')

        open_button = Gtk.Button(label='Import from folder')
        open_button.add_css_class('suggested-action')
        open_button.connect('clicked', lambda *_: self.choose_folder())

        note = self._label(
            'Tip: You can publish your audiobook on sellmybook.app and sell directly to your readers.',
            dim=True,
            xalign=0.5,
        )
        note.set_max_width_chars(64)

        box.append(title)
        box.append(step1)
        box.append(step2)
        box.append(step3)
        box.append(open_button)
        box.append(note)
        outer.append(box)
        outer.append(self._promo_banner())
        return outer

    def _build_editor_page(self) -> Gtk.Widget:
        self.editor_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.editor_scroll.set_child(self._build_editor_content())
        return self.editor_scroll

    def _build_editor_content(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        box.append(self._promo_banner())

        switcher_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        switcher_row.set_halign(Gtk.Align.CENTER)
        self.view_switcher = Gtk.StackSwitcher()
        self.view_switcher.set_stack(self.editor_stack)
        switcher_row.append(self.view_switcher)
        self.switcher_row = switcher_row
        box.append(self.switcher_row)

        # Book Details page
        self.editor_stack.add_titled(
            self._book_details_section(),
            "details",
            "Book Details"
        )

        # Chapters page
        self.editor_stack.add_titled(
            self._chapters_section(),
            "chapters",
            "Chapters"
        )

        stack_frame = Gtk.Frame()
        stack_frame.set_child(self.editor_stack)
        self.editor_stack_frame = stack_frame

        box.append(stack_frame)
        self.actions_section = self._build_actions_section()
        box.append(self.actions_section)
        return box

    def _build_actions_section(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.add_css_class('build-panel')
        outer.set_margin_top(4)
        outer.set_margin_bottom(4)
        outer.set_margin_start(2)
        outer.set_margin_end(2)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=14,
            margin_bottom=14,
            margin_start=14,
            margin_end=14,
        )
        content.set_halign(Gtk.Align.CENTER)

        self.create_mp3_switch = Gtk.Switch(active=False)
        self.create_mp3_switch.connect('notify::active', lambda *_: self.refresh_preview())

        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toggle_row.set_halign(Gtk.Align.CENTER)
        toggle_label = self._label('Create .mp3 File', bold=True)
        toggle_row.append(toggle_label)
        toggle_row.append(self.create_mp3_switch)

        self.action_build_button = Gtk.Button()
        self.action_build_button.add_css_class('suggested-action')
        self.action_build_button.connect('clicked', lambda *_: self.on_build_button_clicked())
        self.action_build_button.set_sensitive(False)

        self.action_build_button_spinner = Gtk.Spinner()
        self.action_build_button_spinner.set_spinning(False)
        self.action_build_button_spinner.set_visible(False)
        self.action_build_button_spinner.set_size_request(16, 16)
        self.action_build_button_spinner.set_valign(Gtk.Align.CENTER)
        self.action_build_button_label = Gtk.Label(label='Build Audiobook')
        self.action_build_button_label.set_valign(Gtk.Align.CENTER)
        action_button_content = Gtk.Box(spacing=8)
        action_button_content.set_valign(Gtk.Align.CENTER)
        action_button_content.append(self.action_build_button_spinner)
        action_button_content.append(self.action_build_button_label)
        self.action_build_button.set_child(action_button_content)
        self.action_build_button.set_halign(Gtk.Align.CENTER)

        output_folder_title = self._label('Export Folder', bold=True, xalign=0.5)
        self.output_folder_label = self._label('', xalign=0)
        self.output_folder_label.add_css_class('mono-label')
        self.output_folder_label.set_xalign(0.5)
        self.export_folder_button = Gtk.Button(label='Choose Export Folder…')
        self.export_folder_button.connect('clicked', lambda *_: self.choose_export_folder())
        self.export_folder_button.set_halign(Gtk.Align.CENTER)

        output_m4b_title = self._label('M4B File', bold=True, xalign=0.5)
        self.output_m4b_label = self._label('', xalign=0)
        self.output_m4b_label.add_css_class('mono-label')
        self.output_m4b_label.set_xalign(0.5)

        self.output_mp3_title = self._label('MP3 File', bold=True, xalign=0.5)
        self.output_mp3_label = self._label('', xalign=0)
        self.output_mp3_label.add_css_class('mono-label')
        self.output_mp3_label.set_xalign(0.5)
        self.estimate_label = self._label('', dim=True, xalign=0.5, justify=Gtk.Justification.CENTER)

        content.append(output_folder_title)
        content.append(self.output_folder_label)
        content.append(self.export_folder_button)
        content.append(output_m4b_title)
        content.append(self.output_m4b_label)
        content.append(toggle_row)
        content.append(self.output_mp3_title)
        content.append(self.output_mp3_label)
        content.append(self.action_build_button)
        content.append(self.estimate_label)

        outer.append(content)
        return outer

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

    def _book_details_section(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        outer.set_valign(Gtk.Align.START)

        top_grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        top_grid.set_valign(Gtk.Align.START)
        
        self.input_dir = Gtk.Entry()
        self.input_dir.set_editable(False)
        self.input_dir.set_hexpand(True)

        self.cover_file = Gtk.Entry()
        self.cover_file.set_editable(False)
        self.cover_file.set_hexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        left.set_valign(Gtk.Align.START)
        left.append(self._readonly_chooser_row('Cover Image (optional)', self.cover_file, 'Choose cover…', self.choose_cover))

        self.title_entry = self._simple_entry('')
        self.artist_entry = self._simple_entry('')
        self.writer_entry = self._simple_entry('')
        self.year_entry = self._simple_entry(str(date.today().year))
        self.narrator_entry = self._simple_entry('')

        self.title_entry.connect('changed', lambda *_: self.refresh_output_hint())

        left_meta = Gtk.FlowBox()
        left_meta.set_selection_mode(Gtk.SelectionMode.NONE)
        left_meta.set_activate_on_single_click(False)
        left_meta.set_column_spacing(16)
        left_meta.set_row_spacing(14)
        left_meta.set_min_children_per_line(1)
        left_meta.set_max_children_per_line(3)
        rows = [
            ('Title', self.title_entry),
            ('Artist', self.artist_entry),
            ('Writer', self.writer_entry),
            ('Year', self.year_entry),
            ('Narrator (optional)', self.narrator_entry),
        ]
        for label_text, widget in rows:
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            field.set_size_request(220, -1)
            field.append(self._label(label_text, bold=True))
            field.append(widget)
            left_meta.insert(field, -1)
        left.append(left_meta)

        desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        desc_box.append(self._label('Description', bold=True))

        self.description_buffer = Gtk.TextBuffer()
        self.description_buffer.connect('changed', lambda *_: self.refresh_preview())

        self.description_view = Gtk.TextView(buffer=self.description_buffer)
        self.description_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.description_view.set_top_margin(12)
        self.description_view.set_bottom_margin(12)
        self.description_view.set_left_margin(12)
        self.description_view.set_right_margin(12)
        self.description_view.set_vexpand(False)

        desc_scroll = Gtk.ScrolledWindow()
        desc_scroll.set_min_content_height(96)
        desc_scroll.set_hexpand(True)
        desc_scroll.set_vexpand(False)
        desc_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        desc_scroll.set_child(self.description_view)

        desc_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        desc_frame.add_css_class('description-editor')
        desc_frame.set_overflow(Gtk.Overflow.HIDDEN)
        desc_frame.append(desc_scroll)
        desc_box.append(desc_frame)
        left.append(desc_box)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_valign(Gtk.Align.START)
        right.append(self._label('Cover preview', bold=True))

        self.cover_picture = Gtk.Picture()
        self.cover_picture.set_size_request(180, 180)
        self.cover_picture.set_can_shrink(True)
        self.cover_picture.set_content_fit(Gtk.ContentFit.COVER)

        self.cover_preview_button = Gtk.Button()
        self.cover_preview_button.set_child(self.cover_picture)
        self.cover_preview_button.set_sensitive(False)
        self.cover_preview_button.connect('clicked', self.show_cover_fullscreen)

        enlarge_hint = self._label('Click to enlarge', dim=True, xalign=0.5)
        enlarge_hint.set_halign(Gtk.Align.FILL)

        right.append(self.cover_preview_button)
        right.append(enlarge_hint)

        top_grid.attach(left, 0, 0, 1, 1)
        top_grid.attach(right, 1, 0, 1, 1)
        outer.append(top_grid)
        return outer

    def _natural_key(self, path: Path):
        parts = re.split(r'(\d+)', path.name.lower())
        return [int(part) if part.isdigit() else part for part in parts]

    def _scan_audio_files(self, folder: Path) -> list[Path]:
        files = [
            p for p in folder.iterdir()
            if p.is_file()
            and p.suffix.lower() in SUPPORTED_AUDIO_EXTS
            and not p.name.startswith('.')
        ]
        return sorted(files, key=self._natural_key)

    def _read_json_file(self, path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _google_books_manifest(self, folder: Path) -> dict | None:
        if not (folder / 'signatures.json').is_file():
            return None
        return self._read_json_file(folder / 'publication.json')

    def _first_text(self, value) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, dict):
            for key in ('value', 'name', 'text'):
                result = self._first_text(value.get(key))
                if result:
                    return result
            return None
        if isinstance(value, list):
            for item in value:
                result = self._first_text(item)
                if result:
                    return result
        return None

    def _manifest_person_name(self, value) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, dict):
            for key in ('name', 'value'):
                result = self._first_text(value.get(key))
                if result:
                    return result
        if isinstance(value, list):
            names = [name for item in value if (name := self._manifest_person_name(item))]
            if names:
                return ', '.join(names)
        return None

    def _parse_iso8601_duration_seconds(self, value) -> float | None:
        if not isinstance(value, str):
            return None
        match = re.fullmatch(
            r'P(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)',
            value.strip(),
        )
        if not match:
            return None

        hours = float(match.group('hours') or 0)
        minutes = float(match.group('minutes') or 0)
        seconds = float(match.group('seconds') or 0)
        return (hours * 3600) + (minutes * 60) + seconds

    def _manifest_chapter_rows(self, folder: Path, manifest: dict) -> list[dict]:
        rows: list[dict] = []
        reading_order = manifest.get('readingOrder')
        if not isinstance(reading_order, list):
            return rows

        for index, item in enumerate(reading_order, start=1):
            if not isinstance(item, dict):
                continue
            url = item.get('url')
            if not isinstance(url, str) or not url.strip():
                continue
            path = (folder / url).resolve()
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
                continue

            title = self._first_text(item.get('name')) or self._default_chapter_title(path)
            duration = self._parse_iso8601_duration_seconds(item.get('duration'))
            if duration is None:
                probed_duration, tag_title = self.get_audio_info(path)
                duration = probed_duration
                if not self._first_text(item.get('name')) and tag_title:
                    title = tag_title

            rows.append({
                'index': index,
                'path': path,
                'title': title,
                'duration': duration,
            })

        return rows

    def _manifest_metadata(self, manifest: dict) -> dict[str, str]:
        metadata: dict[str, str] = {}

        title = self._first_text(manifest.get('name'))
        if title:
            metadata['title'] = title

        creator = manifest.get('creator')
        if isinstance(creator, dict):
            narrator = self._manifest_person_name(creator.get('readBy'))
            if narrator:
                metadata['narrator'] = narrator

            writer = self._manifest_person_name(creator.get('author'))
            if writer:
                metadata['writer'] = writer

            artist = self._manifest_person_name(creator.get('name'))
            if artist:
                metadata['artist'] = artist
        elif creator:
            creator_name = self._manifest_person_name(creator)
            if creator_name:
                metadata['artist'] = creator_name
                metadata['writer'] = creator_name

        for key, manifest_key in (
            ('writer', 'author'),
            ('artist', 'publisher'),
            ('year', 'datePublished'),
            ('description', 'description'),
        ):
            if key in metadata:
                continue
            value = self._first_text(manifest.get(manifest_key))
            if value:
                metadata[key] = value[:4] if key == 'year' else value

        return metadata

    def _chapter_rows_for_folder(self, folder: Path) -> list[dict]:
        manifest = self._google_books_manifest(folder)
        if manifest is not None:
            rows = self._manifest_chapter_rows(folder, manifest)
            if rows:
                return rows

        rows: list[dict] = []
        for index, path in enumerate(self._scan_audio_files(folder), start=1):
            duration, tag_title = self.get_audio_info(path)
            title = tag_title or self._default_chapter_title(path)
            rows.append({
                'index': index,
                'path': path,
                'title': title,
                'duration': duration,
            })
        return rows

    def _default_chapter_title(self, path: Path) -> str:
        stem = path.stem
        stem = re.sub(r'^[\W_]*\d+[\W_ -]*', '', stem).strip()
        return stem or path.stem

    def get_audio_info(self, path: Path) -> tuple[float, str | None]:
        try:
            data = self.ffprobe_json(path)
            duration = self._parse_probe_duration(data)
            title = self._probe_title(data)
            return duration, title
        except Exception:
            return 0.0, None

    def format_duration(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f'{h}:{m:02d}:{s:02d}'
        return f'{m:02d}:{s:02d}'

    def _load_chapters_from_folder(self) -> None:
        folder_text = self.input_dir.get_text().strip()
        self.chapter_rows.clear()

        if not folder_text:
            self._rebuild_chapters_ui()
            return

        folder = Path(folder_text)
        if not folder.is_dir():
            self._rebuild_chapters_ui()
            return

        self.chapter_rows.extend(self._chapter_rows_for_folder(folder))

        self._rebuild_chapters_ui()
        if self.chapter_rows:
            self.set_status(f'Loaded {len(self.chapter_rows)} chapter files.')

    def _capture_editor_scroll_state(self) -> tuple[float, bool] | None:
        if not hasattr(self, 'editor_scroll'):
            return None
        adjustment = self.editor_scroll.get_vadjustment()
        value = adjustment.get_value()
        at_bottom = value + adjustment.get_page_size() >= adjustment.get_upper() - 5
        return value, at_bottom

    def _restore_editor_scroll_state(self, state: tuple[float, bool] | None) -> bool:
        if state is None or not hasattr(self, 'editor_scroll'):
            return False

        value, at_bottom = state
        adjustment = self.editor_scroll.get_vadjustment()
        if at_bottom:
            target = adjustment.get_upper() - adjustment.get_page_size()
        else:
            target = value
        adjustment.set_value(max(adjustment.get_lower(), min(target, adjustment.get_upper() - adjustment.get_page_size())))
        return False

    def _rebuild_chapters_ui(self) -> None:
        scroll_state = self._capture_editor_scroll_state()
        # clear existing children
        child = self.chapter_list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.chapter_list_box.remove(child)
            child = next_child

        if not self.chapter_rows:
            empty = self._label('No chapter audio files found in this folder.', dim=True)
            empty.set_margin_top(8)
            empty.set_margin_bottom(8)
            self.chapter_list_box.append(empty)
            return

        total = len(self.chapter_rows)
        for i, row_data in enumerate(self.chapter_rows, start=1):
            row_data['index'] = i
            row = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=6,
                margin_top=10,
                margin_bottom=10,
                margin_start=10,
                margin_end=10,
            )

            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

            number = self._label(f'Chapter {i}', bold=True)
            number.set_width_chars(10)
            top.append(number)

            file_label = self._label(row_data['path'].name, dim=True)
            file_label.set_hexpand(True)
            top.append(file_label)

            duration_text = self.format_duration(row_data.get('duration', 0))
            duration_label = self._label(duration_text, dim=True)
            top.append(duration_label)

            # Re-ordering and exclusion buttons
            button_box = Gtk.Box(spacing=4)
            
            up_btn = Gtk.Button.new_from_icon_name('go-up-symbolic')
            up_btn.set_tooltip_text('Move Up')
            up_btn.set_sensitive(i > 1)
            up_btn.connect('clicked', lambda *_, r=row_data: self._move_chapter_up(r))
            button_box.append(up_btn)

            down_btn = Gtk.Button.new_from_icon_name('go-down-symbolic')
            down_btn.set_tooltip_text('Move Down')
            down_btn.set_sensitive(i < total)
            down_btn.connect('clicked', lambda *_, r=row_data: self._move_chapter_down(r))
            button_box.append(down_btn)

            remove_btn = Gtk.Button.new_from_icon_name('user-trash-symbolic')
            remove_btn.set_tooltip_text('Exclude from build')
            remove_btn.add_css_class('destructive-action')
            remove_btn.connect('clicked', lambda *_, r=row_data: self._remove_chapter(r))
            button_box.append(remove_btn)

            top.append(button_box)
            row.append(top)

            title_entry = Gtk.Entry()
            title_entry.set_text(row_data['title'])
            title_entry.set_hexpand(True)
            title_entry.connect('changed', self._on_chapter_title_changed, row_data)
            row.append(title_entry)

            self.chapter_list_box.append(row)

        GLib.idle_add(self._restore_editor_scroll_state, scroll_state)

    def _move_chapter_up(self, row_data: dict) -> None:
        idx = self.chapter_rows.index(row_data)
        if idx > 0:
            self.chapter_rows[idx], self.chapter_rows[idx-1] = self.chapter_rows[idx-1], self.chapter_rows[idx]
            self._rebuild_chapters_ui()
            self.refresh_preview()

    def _move_chapter_down(self, row_data: dict) -> None:
        idx = self.chapter_rows.index(row_data)
        if idx < len(self.chapter_rows) - 1:
            self.chapter_rows[idx], self.chapter_rows[idx+1] = self.chapter_rows[idx+1], self.chapter_rows[idx]
            self._rebuild_chapters_ui()
            self.refresh_preview()

    def _remove_chapter(self, row_data: dict) -> None:
        self.chapter_rows.remove(row_data)
        self._rebuild_chapters_ui()
        self.refresh_preview()

    def _on_chapter_title_changed(self, entry: Gtk.Entry, row_data: dict) -> None:
        row_data['title'] = entry.get_text().strip()
        self.refresh_preview()

    def _chapter_title_rows(self) -> list[dict]:
        return self.chapter_rows

    def _chapters_section(self) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        help_text = self._label(
            'Set chapter titles before creating your audiobook. Add chapter files from any folder.',
            dim=True,
        )
        outer.append(help_text)

        add_button = Gtk.Button(label='Add Chapters…')
        add_button.connect('clicked', lambda *_: self.choose_chapter_files())
        add_button.set_margin_top(8)
        add_button.set_margin_bottom(8)
        outer.append(add_button)

        self.chapter_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        frame = Gtk.Frame()
        frame.add_css_class('card')
        frame.set_child(self.chapter_list_box)

        outer.append(frame)

        add_bottom_button = Gtk.Button(label='Add Chapters…')
        add_bottom_button.connect('clicked', lambda *_: self.choose_chapter_files())
        add_bottom_button.set_margin_top(8)
        add_bottom_button.set_margin_bottom(8)
        outer.append(add_bottom_button)
        return outer

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
        import shlex
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

    def _on_folder_selected(self, dialog, result):
        try:
            file = dialog.select_folder_finish(result)
        except Exception:
            return  # cancelled

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

    def _on_export_folder_selected(self, dialog, result):
        try:
            file = dialog.select_folder_finish(result)
        except Exception:
            return

        path = file.get_path()
        if path:
            self.input_dir.set_text(path)
            self._set_project_loaded(True)
            self.refresh_preview()

    def choose_chapter_files(self, *_args) -> None:
        filter = Gtk.FileFilter()
        for ext in SUPPORTED_AUDIO_EXTS:
            filter.add_pattern(f'*{ext}')
            filter.add_pattern(f'*{ext.upper()}')
        filter.set_name('Audio files')

        dialog = Gtk.FileDialog(title='Add chapter files')
        dialog.set_default_filter(filter)
        dialog.open_multiple(self, None, self._on_chapter_files_selected)

    def _on_chapter_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except Exception:
            return

        added = 0
        for i in range(files.get_n_items()):
            file = files.get_item(i)
            if file is None:
                continue
            path_str = file.get_path()
            if not path_str:
                continue
            path = Path(path_str)
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
                continue
            duration, tag_title = self.get_audio_info(path)
            title = tag_title or self._default_chapter_title(path)
            self.chapter_rows.append({
                'index': len(self.chapter_rows) + 1,
                'path': path,
                'title': title,
                'duration': duration,
            })
            added += 1

        if added:
            self._rebuild_chapters_ui()
            self.refresh_preview()
            self.set_status(f'Added {added} chapter file{"s" if added != 1 else ""}.')

    def choose_cover(self, *_args) -> None:
        filter = Gtk.FileFilter()
        filter.add_mime_type('image/jpeg')
        filter.add_mime_type('image/png')
        filter.set_name('Images')

        dialog = Gtk.FileDialog(title='Choose cover image')
        dialog.set_default_filter(filter)
        dialog.open(self, None, self._on_cover_selected)

    def _on_cover_selected(self, dialog, result):
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

    def populate_from_folder(self, *_args, silent: bool = False) -> None:
        folder_text = self.input_dir.get_text().strip()
        if not folder_text:
            if not silent:
                self.show_toast('Import a folder first.')
            return

        folder = Path(folder_text)
        if not folder.is_dir():
            if not silent:
                self.show_toast('That import folder does not exist.')
            return

        manifest = self._google_books_manifest(folder)
        manifest_metadata = self._manifest_metadata(manifest) if manifest is not None else {}

        if manifest_metadata.get('title'):
            self.title_entry.set_text(manifest_metadata['title'])
        elif not self.title_entry.get_text().strip():
            self.title_entry.set_text(folder.name)

        if manifest_metadata.get('artist') and not self.artist_entry.get_text().strip():
            self.artist_entry.set_text(manifest_metadata['artist'])
        if manifest_metadata.get('writer') and not self.writer_entry.get_text().strip():
            self.writer_entry.set_text(manifest_metadata['writer'])
        if manifest_metadata.get('year') and not self.year_entry.get_text().strip():
            self.year_entry.set_text(manifest_metadata['year'])
        if manifest_metadata.get('narrator') and not self.narrator_entry.get_text().strip():
            self.narrator_entry.set_text(manifest_metadata['narrator'])

        # 1. Look for existing image files
        found_image = False
        for candidate_name in ('cover.jpg', 'cover.jpeg', 'cover.png'):
            candidate = folder / candidate_name
            if candidate.exists():
                self.cover_file.set_text(str(candidate))
                found_image = True
                break

        # 2. If no image found, try to extract from first audio file
        if not found_image:
            audio_files = [row['path'] for row in self._chapter_rows_for_folder(folder)]
            if audio_files:
                first_audio = audio_files[0]
                # We'll try to extract a cover using ffmpeg directly.
                # If it succeeds, it will create 'cover.jpg' in the folder.
                potential_cover = folder / 'cover.jpg'
                
                cmd = [
                    bundled_tool('ffmpeg'),
                    '-y',
                    '-i', str(first_audio),
                    '-an',
                    '-vcodec', 'copy',
                    str(potential_cover)
                ]
                
                try:
                    # Run quietly
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0 and potential_cover.exists() and potential_cover.stat().st_size > 0:
                        self.cover_file.set_text(str(potential_cover))
                        found_image = True
                    else:
                        # Clean up failed/empty extraction
                        if potential_cover.exists():
                            potential_cover.unlink()
                except Exception:
                    if potential_cover.exists():
                        potential_cover.unlink()

        if not found_image:
            self.cover_file.set_text('')

        desc = folder / 'description.txt'
        if desc.exists():
            try:
                self.description_buffer.set_text(desc.read_text(encoding='utf-8'))
            except Exception:
                self.description_buffer.set_text(manifest_metadata.get('description', ''))
        elif manifest_metadata.get('description'):
            self.description_buffer.set_text(manifest_metadata['description'])
        else:
            self.description_buffer.set_text('')

        self.refresh_output_hint()
        if not silent:
            self.show_toast('Loaded defaults from folder.')
        self._load_cover_preview()
        self._load_chapters_from_folder()
        self.refresh_preview()

    def safe_filename(self, text: str) -> str:
        text = text.strip()
        if not text:
            return 'audiobook'
        text = re.sub(r'[<>:"/\\|?*]+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text or 'audiobook'

    def get_output_paths(self) -> tuple[Path | None, Path | None]:
        if self._active_output_paths is not None:
            return self._active_output_paths

        folder_text = self.input_dir.get_text().strip()
        if not folder_text:
            return None, None
        folder = Path(folder_text)
        title = self.title_entry.get_text().strip() or folder.name
        safe_title = self.safe_filename(title)
        include_mp3 = self.create_mp3_switch.get_active()
        m4b_path, mp3_path = self._resolved_output_paths(folder, safe_title, include_mp3)
        return m4b_path, mp3_path

    def _resolved_output_paths(self, folder: Path, safe_title: str, include_mp3: bool) -> tuple[Path, Path | None]:
        counter = 1
        while True:
            suffix = '' if counter == 1 else f' ({counter})'
            m4b_path = folder / f'{safe_title}{suffix}.m4b'
            mp3_path = folder / f'{safe_title}{suffix}.mp3' if include_mp3 else None

            paths = [m4b_path]
            if mp3_path is not None:
                paths.append(mp3_path)

            if not any(path.exists() for path in paths):
                return m4b_path, mp3_path

            counter += 1

    def _format_bytes(self, size: int) -> str:
        units = ['bytes', 'KB', 'MB', 'GB', 'TB']
        value = float(max(size, 0))
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == 'bytes':
                    return f'{int(value)} {unit}'
                return f'{value:.1f} {unit}'
            value /= 1024
        return f'{int(size)} bytes'

    def estimated_output_size(self) -> int:
        total = 0
        for row in self.chapter_rows:
            path = row.get('path')
            if path is None:
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def refresh_output_hint(self) -> None:
        m4b_path, mp3_path = self.get_output_paths()
        if m4b_path is None:
            self.output_folder_label.set_text('Choose an export folder to set the export location.')
            self.output_m4b_label.set_text('Choose an export folder to generate the output filename.')
            self.output_mp3_title.set_visible(False)
            self.output_mp3_label.set_visible(False)
            self.estimate_label.set_text('')
            return

        self.output_folder_label.set_text(str(m4b_path.parent))
        self.output_mp3_title.set_visible(mp3_path is not None)
        self.output_mp3_label.set_visible(mp3_path is not None)

        if mp3_path is not None:
            self.output_mp3_label.set_text(mp3_path.name)

        self.output_m4b_label.set_text(m4b_path.name)

        estimate = self.estimated_output_size()
        if estimate > 0:
            self.estimate_label.set_text(
                f'Estimated audiobook size: about {self._format_bytes(estimate)} based on included chapter files.'
            )
        else:
            self.estimate_label.set_text('Estimated audiobook size will appear after chapter files are loaded.')

    def ffprobe_json(self, path: Path) -> dict:
        cmd = [
            bundled_tool('ffprobe'),
            '-v', 'error',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout or '{}')

    def _parse_probe_duration(self, probe_data: dict) -> float:
        format_section = probe_data.get('format') or {}
        duration_text = format_section.get('duration')
        if duration_text:
            try:
                return float(duration_text)
            except (TypeError, ValueError):
                pass

        for stream in probe_data.get('streams') or []:
            duration_text = stream.get('duration')
            if duration_text:
                try:
                    return float(duration_text)
                except (TypeError, ValueError):
                    continue

        return 0.0

    def _probe_title(self, probe_data: dict) -> str | None:
        tags = (probe_data.get('format') or {}).get('tags') or {}
        title = tags.get('title')
        if isinstance(title, str) and title.strip():
            return title.strip()

        for stream in probe_data.get('streams') or []:
            stream_tags = stream.get('tags') or {}
            title = stream_tags.get('title')
            if isinstance(title, str) and title.strip():
                return title.strip()

        return None

    def probe_duration(self, path: Path) -> float:
        return self._parse_probe_duration(self.ffprobe_json(path))

    def ffmetadata_escape(self, value: str) -> str:
        return value.replace('\\', '\\\\').replace('\n', '\\\n').replace(';', r'\;').replace('#', r'\#').replace('=', r'\=')

    def build_concat_file(self, inputs: list[Path]) -> str:
        def escape_concat(path: Path) -> str:
            return str(path).replace("'", r"'\''")

        return ''.join(f"file '{escape_concat(path)}'\n" for path in inputs)

    def build_ffmetadata(self, chapter_entries: list[dict[str, int | str]]) -> str:
        lines = [';FFMETADATA1']

        title = self.title_entry.get_text().strip()
        if title:
            lines.append(f'title={self.ffmetadata_escape(title)}')

        artist = self.artist_entry.get_text().strip()
        if artist:
            escaped = self.ffmetadata_escape(artist)
            lines.append(f'artist={escaped}')
            lines.append(f'album_artist={escaped}')

        writer = self.writer_entry.get_text().strip()
        if writer:
            lines.append(f'composer={self.ffmetadata_escape(writer)}')

        year = self.year_entry.get_text().strip()
        if year:
            lines.append(f'date={self.ffmetadata_escape(year)}')

        description = self._description_text()
        if description:
            escaped = self.ffmetadata_escape(description)
            lines.append(f'comment={escaped}')
            lines.append(f'description={escaped}')

        for chapter in chapter_entries:
            lines.extend([
                '',
                '[CHAPTER]',
                'TIMEBASE=1/1000',
                f'START={chapter["start"]}',
                f'END={chapter["end"]}',
                f'title={self.ffmetadata_escape(str(chapter["title"]))}',
            ])

        lines.append('')
        return '\n'.join(lines)

    def build_final_mux_command(self, build_input_dir: Path, merged_audio: Path, metadata_file: Path, output_path: Path) -> list[str]:
        cmd = [
            bundled_tool('ffmpeg'),
            '-y',
            '-i', str(merged_audio),
            '-i', str(metadata_file),
        ]

        cover_path = self._staged_cover_path(build_input_dir)
        has_cover = cover_path is not None
        if has_cover:
            cmd.extend(['-i', str(cover_path)])

        cmd.extend([
            '-map', '0:a:0',
            '-map_metadata', '1',
        ])

        if has_cover:
            cmd.extend([
                '-map', '2:v:0',
                '-c:v', 'mjpeg',
                '-disposition:v:0', 'attached_pic',
            ])

        cmd.extend([
            '-c:a', 'copy',
            '-movflags', '+faststart',
        ])
        return self._append_output_metadata(cmd, output_path)

    def _append_output_metadata(self, cmd: list[str], output_path: Path) -> list[str]:
        title = self.title_entry.get_text().strip()
        artist = self.artist_entry.get_text().strip()
        writer = self.writer_entry.get_text().strip()
        year = self.year_entry.get_text().strip()
        description = self._description_text()

        if title:
            cmd.extend(['-metadata', f'title={title}'])
            cmd.extend(['-metadata', f'album={title}'])
        if artist:
            cmd.extend(['-metadata', f'artist={artist}'])
            cmd.extend(['-metadata', f'album_artist={artist}'])
        if writer:
            cmd.extend(['-metadata', f'composer={writer}'])
        if year:
            cmd.extend(['-metadata', f'date={year}'])
        if description:
            cmd.extend(['-metadata', f'comment={description}'])
            cmd.extend(['-metadata', f'description={description}'])

        cmd.append(str(output_path))
        return cmd

    def _staged_cover_path(self, build_input_dir: Path) -> Path | None:
        for ext in ('.jpg', '.jpeg', '.png'):
            candidate = build_input_dir / f'cover{ext}'
            if candidate.is_file():
                return candidate
        return None

    def build_tone_command(self) -> list[str] | None:
        m4b_output, _mp3_output = self.get_output_paths()
        assert m4b_output is not None
        cmd = [bundled_tool('tone'), 'tag', str(m4b_output)]

        title = self.title_entry.get_text().strip()
        artist = self.artist_entry.get_text().strip()
        writer = self.writer_entry.get_text().strip()
        narrator = self.narrator_entry.get_text().strip()
        description = self._description_text()

        if title:
            cmd.extend(['--meta-title', title, '--meta-album', title])
        if artist:
            cmd.extend(['--meta-artist', artist, '--meta-album-artist', artist])
        if writer:
            cmd.extend(['--meta-composer', writer])
        if description:
            cmd.extend(['--meta-comment', description, '--meta-description', description])
        if narrator:
            cmd.extend(['--meta-narrator', narrator])

        return cmd if len(cmd) > 3 else None

    def build_mp3_command(self) -> list[str] | None:
        m4b_output, mp3_output = self.get_output_paths()
        assert m4b_output is not None
        if mp3_output is not None:
            return [
                bundled_tool('ffmpeg'),
                '-y',
                '-i', str(m4b_output),
                '-vn',
                '-codec:a', 'libmp3lame',
                '-q:a', '2',
                str(mp3_output),
            ]
        return None

    def build_preview_lines(self) -> list[str]:
        m4b_output, mp3_output = self.get_output_paths()
        assert m4b_output is not None

        lines = [
            'Pipeline:',
            '1. Stage chapter files into a temporary build folder with stable numbering.',
            '2. Transcode each staged chapter to AAC `.m4a` using FFmpeg.',
            '3. Merge the AAC files with the FFmpeg concat demuxer.',
            '4. Generate an `FFMETADATA1` file with global tags and chapter markers.',
            '5. Mux the final `.m4b` with FFmpeg and attach the cover image when present.',
            '',
            'Representative commands:',
            self.shell_join([
                bundled_tool('ffmpeg'),
                '-y',
                '-i', '<chapter>',
                '-map', '0:a:0',
                '-vn',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-metadata', 'title=<chapter-title>',
                '<staging>/001.m4a',
            ]),
            self.shell_join([
                bundled_tool('ffmpeg'),
                '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', '<staging>/concat.txt',
                '-c', 'copy',
                '<staging>/merged.m4a',
            ]),
            self.shell_join(self.build_final_mux_command(Path('<staging>'), Path('<staging>/merged.m4a'), Path('<staging>/metadata.ffmeta'), m4b_output)),
        ]

        tone_cmd = self.build_tone_command()
        if tone_cmd:
            lines.extend(['', 'Metadata command:', self.shell_join(tone_cmd)])
        else:
            lines.extend(['', 'Metadata command:', '(skipped)'])

        mp3_cmd = self.build_mp3_command()
        if mp3_cmd:
            lines.extend(['', 'MP3 conversion command:', self.shell_join(mp3_cmd)])
        else:
            lines.extend(['', 'MP3 conversion command:', '(not enabled)'])

        if mp3_output is not None:
            lines.extend(['', f'Outputs: {m4b_output} and {mp3_output}'])
        else:
            lines.extend(['', f'Output: {m4b_output}'])

        return lines

    def refresh_preview(self) -> None:
        self.refresh_output_hint()

        if not self.input_dir.get_text().strip():
            self.preview_buffer.set_text('Choose an export folder to see the build commands.')
            return

        self.preview_buffer.set_text('\n'.join(self.build_preview_lines()))

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

    def stage_support_files(self, progress_callback=None) -> tuple[Path, list[dict]]:
        source_folder = Path(self.input_dir.get_text().strip())
        description_text = self._description_text()

        if not source_folder.is_dir():
            raise RuntimeError('Audiobook folder does not exist.')

        self._staging_dir = Path(tempfile.mkdtemp(prefix='smb-audio-'))
        staging = self._staging_dir

        chapters = self._chapter_title_rows()
        if not chapters:
            raise RuntimeError('No chapter audio files were found to build.')

        staged_chapters: list[dict] = []
        total_chapters = len(chapters)
        for i, row in enumerate(chapters):
            if self.worker is not None and self.worker.cancelled.is_set():
                raise RuntimeError('Build cancelled.')
            if progress_callback:
                progress_callback((i / total_chapters) * 0.1)

            src = row['path']
            chapter_title = (row.get('title') or '').strip() or self._default_chapter_title(src)
            safe_title = self.safe_filename(chapter_title)
            dst = staging / f'{row["index"]:03d} - {safe_title}{src.suffix.lower()}'

            # Use more selective mapping to be robust against weird metadata or data streams
            cmd = [
                bundled_tool('ffmpeg'),
                '-y',
                '-i', str(src),
                '-map', '0:a',
                '-map', '0:v?',
                '-ignore_unknown',
                '-c', 'copy',
                '-metadata', f'title={chapter_title}',
                str(dst),
            ]
            self.append_log(self.shell_join(cmd))
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if self.worker is not None:
                self.worker.process = process

            output_lines: list[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                if self.worker is not None and self.worker.cancelled.is_set():
                    process.terminate()
                line_text = line.rstrip()
                output_lines.append(line_text)
                self.append_log(line_text)

            result_code = process.wait()
            if self.worker is not None:
                self.worker.process = None

            if self.worker is not None and self.worker.cancelled.is_set():
                raise RuntimeError('Build cancelled.')

            if result_code != 0:
                stderr_snippet = '\n'.join(output_lines)[-400:]
                raise RuntimeError(f'Failed to prepare chapter file: {src.name}\n\nError: {stderr_snippet}')
            staged_chapters.append({
                'index': row['index'],
                'title': chapter_title,
                'duration': row.get('duration') or 0.0,
                'staged_path': dst,
            })

        # stage cover
        cover_src = self.cover_file.get_text().strip()
        if cover_src:
            cover_path = Path(cover_src)
            if cover_path.is_file():
                ext = cover_path.suffix.lower() or '.jpg'
                shutil.copy2(cover_path, staging / f'cover{ext}')
                self.append_log(f'Copied cover to {staging / f"cover{ext}"}')

        # stage description
        if description_text:
            desc_dst = staging / 'description.txt'
            desc_dst.write_text(description_text + '\n', encoding='utf-8')
            self.append_log(f'Wrote description to {desc_dst}')

        return staging, staged_chapters

    def check_tools(self):
        missing = []
        tools = ['ffmpeg', 'ffprobe', 'tone']

        for tool in tools:
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

        # Commands will be generated inside the thread after staging
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
        self.details_button.set_visible(True)  # Make details button visible when build ends
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


if __name__ == '__main__':
    app = M4BApplication()
    raise SystemExit(app.run())
