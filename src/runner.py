from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib

from paths import bundled_tool

if TYPE_CHECKING:
    from window import MainWindow


class CommandRunner(threading.Thread):
    def __init__(self, app: 'MainWindow'):
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
            self._append_log(line.rstrip())

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
