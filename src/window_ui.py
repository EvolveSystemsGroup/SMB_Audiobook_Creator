from __future__ import annotations

from datetime import date

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk


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

    self.editor_stack.add_titled(self._book_details_section(), 'details', 'Book Details')
    self.editor_stack.add_titled(self._chapters_section(), 'chapters', 'Chapters')

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
    for label_text, widget in [
        ('Title', self.title_entry),
        ('Artist', self.artist_entry),
        ('Writer', self.writer_entry),
        ('Year', self.year_entry),
        ('Narrator (optional)', self.narrator_entry),
    ]:
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
