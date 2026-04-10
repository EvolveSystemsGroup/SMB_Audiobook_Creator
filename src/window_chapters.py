from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gtk

from constants import SUPPORTED_AUDIO_EXTS
from paths import bundled_tool


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

        duration_label = self._label(self.format_duration(row_data.get('duration', 0)), dim=True)
        top.append(duration_label)

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
        self.chapter_rows[idx], self.chapter_rows[idx - 1] = self.chapter_rows[idx - 1], self.chapter_rows[idx]
        self._rebuild_chapters_ui()
        self.refresh_preview()


def _move_chapter_down(self, row_data: dict) -> None:
    idx = self.chapter_rows.index(row_data)
    if idx < len(self.chapter_rows) - 1:
        self.chapter_rows[idx], self.chapter_rows[idx + 1] = self.chapter_rows[idx + 1], self.chapter_rows[idx]
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


def choose_chapter_files(self, *_args) -> None:
    file_filter = Gtk.FileFilter()
    for ext in SUPPORTED_AUDIO_EXTS:
        file_filter.add_pattern(f'*{ext}')
        file_filter.add_pattern(f'*{ext.upper()}')
    file_filter.set_name('Audio files')

    dialog = Gtk.FileDialog(title='Add chapter files')
    dialog.set_default_filter(file_filter)
    dialog.open_multiple(self, None, self._on_chapter_files_selected)


def _on_chapter_files_selected(self, dialog, result) -> None:
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

    found_image = False
    for candidate_name in ('cover.jpg', 'cover.jpeg', 'cover.png'):
        candidate = folder / candidate_name
        if candidate.exists():
            self.cover_file.set_text(str(candidate))
            found_image = True
            break

    if not found_image:
        audio_files = [row['path'] for row in self._chapter_rows_for_folder(folder)]
        if audio_files:
            first_audio = audio_files[0]
            potential_cover = folder / 'cover.jpg'
            cmd = [
                bundled_tool('ffmpeg'),
                '-y',
                '-i', str(first_audio),
                '-an',
                '-vcodec', 'copy',
                str(potential_cover),
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and potential_cover.exists() and potential_cover.stat().st_size > 0:
                    self.cover_file.set_text(str(potential_cover))
                    found_image = True
                elif potential_cover.exists():
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
