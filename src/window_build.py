from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from paths import bundled_tool


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
    return self._resolved_output_paths(folder, safe_title, include_mp3)


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

    cover_src = self.cover_file.get_text().strip()
    if cover_src:
        cover_path = Path(cover_src)
        if cover_path.is_file():
            ext = cover_path.suffix.lower() or '.jpg'
            shutil.copy2(cover_path, staging / f'cover{ext}')
            self.append_log(f'Copied cover to {staging / f"cover{ext}"}')

    if description_text:
        desc_dst = staging / 'description.txt'
        desc_dst.write_text(description_text + '\n', encoding='utf-8')
        self.append_log(f'Wrote description to {desc_dst}')

    return staging, staged_chapters
