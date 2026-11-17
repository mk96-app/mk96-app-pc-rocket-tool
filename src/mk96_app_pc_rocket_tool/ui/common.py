"""Shared UI pieces: message boxes, opening links and folders, the minus/plus stepper and the sound
picker."""

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool import paths
from mk96_app_pc_rocket_tool.sound_library import (
    SOUND_EXTENSIONS,
    SoundEntry,
    add_user_sound,
    list_bundled_sounds,
    list_user_sounds,
)

logger = logging.getLogger(__name__)

WEB_SCHEMES = ("http", "https")
SOUND_ID_ROLE = Qt.ItemDataRole.UserRole
PREVIEW_COLUMN = 1


def standard_icon(widget: QWidget, icon: QStyle.StandardPixmap) -> QIcon:
    """Native Windows icon (play, arrows...): text glyphs like ▶ render tiny in Segoe UI."""
    return widget.style().standardIcon(icon)


def play_button(text: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setIcon(standard_icon(button, QStyle.StandardPixmap.SP_MediaPlay))
    return button


class Stepper(QWidget):
    """Whole number between bounds edited with minus and plus buttons around a centered value (the
    native spin box arrows read as "move" rather than "add/remove"). The value can also be
    typed; the buttons repeat while held and disable themselves at the bounds."""

    valueChanged = Signal(int)
    MINUS_SIGN = chr(0x2212)  # typographic minus: same width as "+", unlike a hyphen
    BUTTON_SIZE = 28
    SIGN_SCALE = 1.35
    VALUE_WIDTH = 48

    def __init__(self, minimum: int, maximum: int, value: int):
        super().__init__()
        self._spin = QSpinBox()
        self._spin.setRange(minimum, maximum)
        self._spin.setValue(value)
        self._spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spin.setFixedSize(self.VALUE_WIDTH, self.BUTTON_SIZE)
        self._minus = self._button(self.MINUS_SIGN, "Quitar uno", self._spin.stepDown)
        self._plus = self._button("+", "Añadir uno", self._spin.stepUp)
        self._spin.valueChanged.connect(self._on_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._minus)
        layout.addWidget(self._spin)
        layout.addWidget(self._plus)
        self._sync_buttons()

    def _button(self, text: str, tooltip: str, action: Callable[[], None]) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        font = button.font()
        font.setPointSizeF(font.pointSizeF() * self.SIGN_SCALE)
        button.setFont(font)
        button.setAutoRepeat(True)
        button.clicked.connect(action)
        return button

    def value(self) -> int:
        return self._spin.value()

    def setValue(self, value: int) -> None:  # Qt naming, like QSpinBox
        self._spin.setValue(value)

    def _on_changed(self, value: int) -> None:
        self._sync_buttons()
        self.valueChanged.emit(value)

    def _sync_buttons(self) -> None:
        self._minus.setEnabled(self._spin.value() > self._spin.minimum())
        self._plus.setEnabled(self._spin.value() < self._spin.maximum())


def show_error(parent: QWidget | None, title: str, body: str) -> None:
    QMessageBox.critical(parent, title, body)


def confirm_destructive(parent: QWidget | None, title: str, body: str, action_label: str) -> bool:
    """Yes/no question whose safe answer (Cancelar) is the default."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, body, parent=parent)
    accept = box.addButton(action_label, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is accept


def open_web_link(url: str) -> bool:
    """Opens an http(s) link in the default browser. Other schemes are refused, since
    meeting links come from user text and must not launch arbitrary handlers."""
    qurl = QUrl.fromUserInput(url.strip())
    if not qurl.isValid() or qurl.scheme().lower() not in WEB_SCHEMES:
        logger.warning("Refused to open non-web link %r", url)
        return False
    return QDesktopServices.openUrl(qurl)


def open_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    """Direct children of a tree item (use invisibleRootItem() for the top level)."""
    return [child for i in range(item.childCount()) if (child := item.child(i)) is not None]


def all_sounds() -> list[SoundEntry]:
    return list_user_sounds(paths.user_sounds_dir()) + list_bundled_sounds(paths.BUNDLED_SOUNDS_DIR)


class SoundPickerDialog(QDialog):
    """Collapsible tree of sounds (own sounds first, then the bundled palette by category),
    a live search box, a ▶ column to try each sound, and a button to add one from disk."""

    def __init__(
        self, parent: QWidget | None, current_sound_id: str | None, preview: Callable[[str], None]
    ):
        super().__init__(parent)
        self.setWindowTitle("Elegir sonido")
        self.resize(460, 540)
        self._preview = preview
        self._chosen: str | None = None

        self._search = QLineEdit(placeholderText="Buscar sonido...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(PREVIEW_COLUMN, QHeaderView.ResizeMode.Fixed)
        self._tree.header().resizeSection(PREVIEW_COLUMN, 36)
        self._tree.header().setStretchLastSection(False)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(lambda item, _col: self._accept_item(item))
        self._tree.currentItemChanged.connect(lambda *_: self._update_ok())

        add_button = QPushButton("Añadir desde mi equipo...")
        add_button.clicked.connect(self._add_from_disk)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Seleccionar")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self._buttons.accepted.connect(lambda: self._accept_item(self._tree.currentItem()))
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addWidget(self._tree)
        layout.addWidget(add_button)
        layout.addWidget(self._buttons)
        self._populate(current_sound_id)
        self._search.setFocus()

    @property
    def chosen_sound_id(self) -> str | None:
        return self._chosen

    def _populate(self, select_sound_id: str | None) -> None:
        self._tree.clear()
        categories: dict[str, QTreeWidgetItem] = {}
        play_icon = standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay)
        selected: QTreeWidgetItem | None = None
        for sound in all_sounds():
            parent = categories.get(sound.category)
            if parent is None:
                parent = QTreeWidgetItem([sound.category])
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._tree.addTopLevelItem(parent)
                categories[sound.category] = parent
            item = QTreeWidgetItem([sound.name])
            item.setData(0, SOUND_ID_ROLE, sound.sound_id)
            item.setIcon(PREVIEW_COLUMN, play_icon)
            item.setToolTip(PREVIEW_COLUMN, "Probar este sonido")
            parent.addChild(item)
            if sound.sound_id == select_sound_id:
                parent.setExpanded(True)
                selected = item
        if selected is not None:
            self._tree.setCurrentItem(selected)
            self._tree.scrollToItem(selected, QTreeWidget.ScrollHint.PositionAtCenter)
        self._update_ok()

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for category in children(self._tree.invisibleRootItem()):
            category_matches = query in category.text(0).lower()
            visible_children = 0
            for child in children(category):
                visible = not query or category_matches or query in child.text(0).lower()
                child.setHidden(not visible)
                visible_children += visible
            category.setHidden(visible_children == 0)
            category.setExpanded(bool(query) and visible_children > 0)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        sound_id = item.data(0, SOUND_ID_ROLE)
        if sound_id and column == PREVIEW_COLUMN:
            self._preview(sound_id)

    def _update_ok(self) -> None:
        item = self._tree.currentItem()
        has_sound = item is not None and bool(item.data(0, SOUND_ID_ROLE))
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(has_sound)

    def _accept_item(self, item: QTreeWidgetItem | None) -> None:
        sound_id = item.data(0, SOUND_ID_ROLE) if item is not None else None
        if sound_id:
            self._chosen = sound_id
            self.accept()

    def _add_from_disk(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in SOUND_EXTENSIONS)
        filename, _ = QFileDialog.getOpenFileName(
            self, "Elegir sonido propio", str(Path.home()), f"Audio ({patterns})"
        )
        if not filename:
            return
        try:
            sound_id = add_user_sound(Path(filename), paths.user_sounds_dir())
        except (ValueError, OSError) as exc:
            show_error(self, "No se pudo añadir el sonido", str(exc))
            return
        self._search.clear()
        self._populate(sound_id)
