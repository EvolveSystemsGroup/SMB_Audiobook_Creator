from __future__ import annotations

from window_base import MainWindowBase
import window_build
import window_chapters
import window_ui


class MainWindow(MainWindowBase):
    _build_welcome_page = window_ui._build_welcome_page
    _build_editor_page = window_ui._build_editor_page
    _build_editor_content = window_ui._build_editor_content
    _build_actions_section = window_ui._build_actions_section
    _book_details_section = window_ui._book_details_section
    _chapters_section = window_ui._chapters_section

    _natural_key = window_chapters._natural_key
    _scan_audio_files = window_chapters._scan_audio_files
    _read_json_file = window_chapters._read_json_file
    _google_books_manifest = window_chapters._google_books_manifest
    _first_text = window_chapters._first_text
    _manifest_person_name = window_chapters._manifest_person_name
    _parse_iso8601_duration_seconds = window_chapters._parse_iso8601_duration_seconds
    _manifest_chapter_rows = window_chapters._manifest_chapter_rows
    _manifest_metadata = window_chapters._manifest_metadata
    _chapter_rows_for_folder = window_chapters._chapter_rows_for_folder
    _default_chapter_title = window_chapters._default_chapter_title
    get_audio_info = window_chapters.get_audio_info
    format_duration = window_chapters.format_duration
    _load_chapters_from_folder = window_chapters._load_chapters_from_folder
    _capture_editor_scroll_state = window_chapters._capture_editor_scroll_state
    _restore_editor_scroll_state = window_chapters._restore_editor_scroll_state
    _rebuild_chapters_ui = window_chapters._rebuild_chapters_ui
    _move_chapter_up = window_chapters._move_chapter_up
    _move_chapter_down = window_chapters._move_chapter_down
    _remove_chapter = window_chapters._remove_chapter
    _on_chapter_title_changed = window_chapters._on_chapter_title_changed
    _chapter_title_rows = window_chapters._chapter_title_rows
    choose_chapter_files = window_chapters.choose_chapter_files
    _on_chapter_files_selected = window_chapters._on_chapter_files_selected
    populate_from_folder = window_chapters.populate_from_folder

    safe_filename = window_build.safe_filename
    get_output_paths = window_build.get_output_paths
    _resolved_output_paths = window_build._resolved_output_paths
    _format_bytes = window_build._format_bytes
    estimated_output_size = window_build.estimated_output_size
    refresh_output_hint = window_build.refresh_output_hint
    ffprobe_json = window_build.ffprobe_json
    _parse_probe_duration = window_build._parse_probe_duration
    _probe_title = window_build._probe_title
    probe_duration = window_build.probe_duration
    ffmetadata_escape = window_build.ffmetadata_escape
    build_concat_file = window_build.build_concat_file
    build_ffmetadata = window_build.build_ffmetadata
    build_final_mux_command = window_build.build_final_mux_command
    _append_output_metadata = window_build._append_output_metadata
    _staged_cover_path = window_build._staged_cover_path
    build_tone_command = window_build.build_tone_command
    build_mp3_command = window_build.build_mp3_command
    build_preview_lines = window_build.build_preview_lines
    refresh_preview = window_build.refresh_preview
    stage_support_files = window_build.stage_support_files
