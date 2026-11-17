"""Day/Week timeline and Month grid of the meetings tab, painted with QPainter.

All recurrence, overlap and geometry decisions come from meetings.py (day_blocks,
day_occurrences, selection_range, now_position...); this module only draws them and maps
mouse positions back. Left-drag selects a time range (Day/Week); right click opens the menu
of a meeting or of the selected range; double click edits a meeting.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QScrollArea, QToolTip, QVBoxLayout, QWidget

from mk96_app_pc_rocket_tool.meetings import (
    LANES_PER_DAY,
    TIMELINE_ROWS,
    TIMELINE_SLOT_MIN,
    TIMELINE_START_HOUR,
    WEEKDAY_SHORT_NAMES,
    OccurrenceStatus,
    PlacedBlock,
    day_blocks,
    day_occurrences,
    default_start_on_day,
    meeting_tooltip,
    now_position,
    occurrence_status,
    selection_range,
    visible_pills,
)
from mk96_app_pc_rocket_tool.models import Meeting

SLOT_HEIGHT = 24
HOUR_COLUMN_WIDTH = 56
HEADER_HEIGHT = 30
DAY_GAP = 3  # px between meeting blocks and the line that separates days
MONTH_WEEKS = 6
PILL_HEIGHT = 20
NOW_COLOR = QColor("#e81123")
INACTIVE_COLOR = QColor("#8b949e")


@dataclass(frozen=True, slots=True)
class CalendarCallbacks:
    on_meeting_menu: Callable[[Meeting, datetime, QPoint], None]  # right click on a meeting
    on_meeting_edit: Callable[[Meeting], None]  # double click on a meeting
    on_empty_menu: Callable[[datetime, int | None, QPoint], None]  # start, duration or None
    on_show_day: Callable[[date], None]  # "+N más" in the Month view


def _accent(widget: QWidget) -> QColor:
    return widget.palette().color(QPalette.ColorRole.Accent)


def _with_alpha(color: QColor, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


def _draw_meeting_box(
    painter: QPainter, rect: QRectF, text: str, inactive: bool, widget: QWidget
) -> None:
    base = INACTIVE_COLOR if inactive else _accent(widget)
    painter.setPen(QPen(_with_alpha(base, 0.9), 1))
    painter.setBrush(_with_alpha(base, 0.22 if inactive else 0.38))
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(INACTIVE_COLOR if inactive else widget.palette().color(QPalette.ColorRole.Text))
    inner = rect.adjusted(5, 1, -4, -1)
    elided = QFontMetrics(painter.font()).elidedText(
        text, Qt.TextElideMode.ElideRight, int(inner.width())
    )
    painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, elided)


def _block_label(block: PlacedBlock) -> str:
    text = f"{block.occurrence:%H:%M} {block.meeting.title}"
    if block.rows.clipped_top:
        text = "▲ " + text
    if block.rows.clipped_bottom:
        text += " ▼"
    return text


class _DayHeader(QWidget):
    def __init__(self, days: list[date], today: date, grid_width: Callable[[], int]):
        """grid_width: current width of the grid below, so columns line up even when the
        grid's vertical scrollbar takes space."""
        super().__init__()
        self._days = days
        self._today = today
        self._grid_width = grid_width
        self.setFixedHeight(HEADER_HEIGHT)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        day_width = (self._grid_width() - HOUR_COLUMN_WIDTH) / len(self._days)
        for index, day in enumerate(self._days):
            rect = QRectF(
                HOUR_COLUMN_WIDTH + index * day_width + 2, 2, day_width - 4, HEADER_HEIGHT - 4
            )
            font = QFont(self.font())
            if day == self._today:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_with_alpha(_accent(self), 0.35))
                painter.drawRoundedRect(rect, 6, 6)
                font.setBold(True)
            painter.setFont(font)
            painter.setPen(self.palette().color(QPalette.ColorRole.Text))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                f"{WEEKDAY_SHORT_NAMES[day.weekday()]} {day.day:02d}",
            )
        painter.end()


class _TimelineGrid(QWidget):
    resized = Signal()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    def __init__(self, days: list[date], meetings: list[Meeting], callbacks: CalendarCallbacks):
        super().__init__()
        self._days = days
        self._callbacks = callbacks
        self._blocks = [day_blocks(meetings, day) for day in days]
        self._now = datetime.now()
        self._selection: tuple[int, int, int] | None = None  # (day index, first row, last row)
        self._drag_origin: tuple[int, int] | None = None
        self.setMinimumHeight(TIMELINE_ROWS * SLOT_HEIGHT + 1)
        self.setMouseTracking(False)

    def sizeHint(self) -> QSize:
        return QSize(600, TIMELINE_ROWS * SLOT_HEIGHT + 1)

    def tick(self) -> None:
        self._now = datetime.now()
        self.update()

    # -- Geometry -------------------------------------------------------------------------------

    def _day_width(self) -> float:
        return (self.width() - HOUR_COLUMN_WIDTH) / len(self._days)

    def _cell_at(self, pos: QPoint) -> tuple[int, int] | None:
        if pos.x() < HOUR_COLUMN_WIDTH:
            return None
        index = min(
            len(self._days) - 1, max(0, int((pos.x() - HOUR_COLUMN_WIDTH) / self._day_width()))
        )
        row = min(TIMELINE_ROWS - 1, max(0, pos.y() // SLOT_HEIGHT))
        return index, row

    def _block_rect(self, day_index: int, block: PlacedBlock) -> QRectF:
        day_width = self._day_width()
        lane_width = day_width / LANES_PER_DAY
        left = HOUR_COLUMN_WIDTH + day_index * day_width + block.first_column * lane_width
        width = block.column_span * lane_width
        # Gap only at the day edges, so overlapping lanes keep their width.
        left_gap = DAY_GAP if block.first_column == 0 else 1
        right_gap = DAY_GAP if block.first_column + block.column_span >= LANES_PER_DAY else 1
        top = block.rows.first_row * SLOT_HEIGHT + 1
        return QRectF(
            left + left_gap,
            top,
            width - left_gap - right_gap,
            block.rows.row_count * SLOT_HEIGHT - 2,
        )

    def _block_at(self, pos: QPoint) -> PlacedBlock | None:
        for day_index, blocks in enumerate(self._blocks):
            for block in reversed(blocks):
                if self._block_rect(day_index, block).contains(pos.toPointF()):
                    return block
        return None

    def anchor_y(self) -> int:
        """Where to scroll on open: two rows above "now" when today is shown, else 08:00."""
        position = now_position(self._now)
        if self._now.date() in self._days and position is not None:
            return max(0, int(position) - 2) * SLOT_HEIGHT
        return 2 * SLOT_HEIGHT

    # -- Painting -------------------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.Text)
        line = _with_alpha(text_color, 0.08)
        hour_line = _with_alpha(text_color, 0.2)
        day_width = self._day_width()
        height = TIMELINE_ROWS * SLOT_HEIGHT

        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Base))
        for index, day in enumerate(self._days):
            if day == self._now.date():
                painter.fillRect(
                    QRectF(HOUR_COLUMN_WIDTH + index * day_width, 0, day_width, height),
                    _with_alpha(_accent(self), 0.07),
                )
        if self._selection is not None:
            index, first, last = self._selection
            low, high = sorted((first, last))
            painter.fillRect(
                QRectF(
                    HOUR_COLUMN_WIDTH + index * day_width,
                    low * SLOT_HEIGHT,
                    day_width,
                    (high - low + 1) * SLOT_HEIGHT,
                ),
                _with_alpha(_accent(self), 0.3),
            )

        hour_font = QFont(self.font())
        hour_font.setPointSizeF(hour_font.pointSizeF() * 0.9)
        for row in range(TIMELINE_ROWS + 1):
            y = row * SLOT_HEIGHT
            is_hour = row % 2 == 0
            painter.setPen(QPen(hour_line if is_hour else line, 1))
            painter.drawLine(HOUR_COLUMN_WIDTH, y, self.width(), y)
            if is_hour and row < TIMELINE_ROWS:
                painter.setFont(hour_font)
                painter.setPen(_with_alpha(text_color, 0.6))
                hour = TIMELINE_START_HOUR + row * TIMELINE_SLOT_MIN // 60
                painter.drawText(
                    QRectF(0, y + 2, HOUR_COLUMN_WIDTH - 8, SLOT_HEIGHT),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                    f"{hour:02d}:00",
                )
        painter.setPen(QPen(hour_line, 1))
        for index in range(len(self._days) + 1):
            x = HOUR_COLUMN_WIDTH + index * day_width
            painter.drawLine(int(x), 0, int(x), height)

        painter.setFont(self.font())
        for day_index, blocks in enumerate(self._blocks):
            for block in blocks:
                inactive = (
                    occurrence_status(block.meeting, block.occurrence) is OccurrenceStatus.INACTIVE
                )
                _draw_meeting_box(
                    painter, self._block_rect(day_index, block), _block_label(block), inactive, self
                )

        self._paint_now_line(painter, day_width)
        painter.end()

    def _paint_now_line(self, painter: QPainter, day_width: float) -> None:
        if self._now.date() not in self._days:
            return
        position = now_position(self._now)
        if position is None:
            return
        index = self._days.index(self._now.date())
        y = position * SLOT_HEIGHT
        left = HOUR_COLUMN_WIDTH + index * day_width
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(NOW_COLOR)
        painter.drawRect(QRectF(left, y - 1, day_width, 2))
        painter.drawEllipse(QRectF(left - 4, y - 4, 8, 8))

    # -- Mouse ----------------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        block = self._block_at(pos)
        cell = self._cell_at(pos)
        if event.button() is Qt.MouseButton.RightButton:
            if block is not None:
                self._callbacks.on_meeting_menu(
                    block.meeting, block.occurrence, event.globalPosition().toPoint()
                )
            elif cell is not None:
                self._open_empty_menu(cell, event.globalPosition().toPoint())
            return
        if event.button() is Qt.MouseButton.LeftButton and block is None and cell is not None:
            self._drag_origin = cell
            self._selection = (cell[0], cell[1], cell[1])
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is None:
            return
        cell = self._cell_at(event.position().toPoint())
        if cell is not None:
            day_index, first_row = self._drag_origin  # a drag stays in the day where it started
            self._selection = (day_index, first_row, cell[1])
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._drag_origin = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        block = self._block_at(event.position().toPoint())
        if block is not None:
            self._callbacks.on_meeting_edit(block.meeting)

    def _open_empty_menu(self, cell: tuple[int, int], global_pos: QPoint) -> None:
        index, row = cell
        selection = self._selection
        if (
            selection is None
            or selection[0] != index
            or not min(selection[1:]) <= row <= max(selection[1:])
        ):
            # Right click outside the selection: the menu is about that single slot.
            selection = self._selection = (index, row, row)
            self.update()
        start, duration = selection_range(self._days[selection[0]], selection[1], selection[2])
        self._callbacks.on_empty_menu(start, duration, global_pos)

    def event(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            block = self._block_at(event.pos())
            if block is not None:
                QToolTip.showText(
                    event.globalPos(), meeting_tooltip(block.meeting, block.occurrence), self
                )
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class TimelineView(QWidget):
    """Day or Week view: day header + scrollable time grid."""

    def __init__(self, days: list[date], meetings: list[Meeting], callbacks: CalendarCallbacks):
        super().__init__()
        self._grid = _TimelineGrid(days, meetings, callbacks)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._grid)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if len(days) > 1:
            header = _DayHeader(days, datetime.now().date(), self._grid.width)
            self._grid.resized.connect(header.update)
            layout.addWidget(header)
        layout.addWidget(self._scroll, 1)

    def tick(self) -> None:
        self._grid.tick()

    def scroll_to_anchor(self) -> None:
        self._scroll.verticalScrollBar().setValue(self._grid.anchor_y())


@dataclass(frozen=True, slots=True)
class _MonthHit:
    rect: QRectF
    day: date
    meeting: Meeting | None = None
    occurrence: datetime | None = None
    hidden_count: int = 0  # > 0: this is the "+N más" link


class MonthView(QWidget):
    def __init__(
        self, year: int, month: int, meetings: list[Meeting], callbacks: CalendarCallbacks
    ):
        super().__init__()
        self._month = month
        self._meetings = meetings
        self._callbacks = callbacks
        first = date(year, month, 1)
        self._first_monday = first - timedelta(days=first.weekday())
        self._today = datetime.now().date()
        self.setMinimumSize(560, 420)

    def tick(self) -> None:
        if datetime.now().date() != self._today:
            self._today = datetime.now().date()
            self.update()

    def scroll_to_anchor(self) -> None:
        """The month always fits: nothing to scroll."""

    def _cell_rect(self, week: int, weekday: int) -> QRectF:
        width = self.width() / 7
        height = (self.height() - HEADER_HEIGHT) / MONTH_WEEKS
        return QRectF(weekday * width + 2, HEADER_HEIGHT + week * height + 2, width - 4, height - 4)

    def _layout(self) -> tuple[list[tuple[QRectF, date]], list[_MonthHit]]:
        cells, hits = [], []
        line_height = QFontMetrics(self.font()).height() + 4
        for week in range(MONTH_WEEKS):
            for weekday in range(7):
                day = self._first_monday + timedelta(days=week * 7 + weekday)
                rect = self._cell_rect(week, weekday)
                cells.append((rect, day))
                occurrences = day_occurrences(self._meetings, day)
                shown, hidden = visible_pills(len(occurrences))
                y = rect.top() + line_height
                for occurrence, meeting in occurrences[:shown]:
                    pill = QRectF(rect.left() + 3, y, rect.width() - 6, PILL_HEIGHT)
                    hits.append(_MonthHit(pill, day, meeting, occurrence))
                    y += PILL_HEIGHT + 2
                if hidden:
                    more = QRectF(rect.left() + 3, y, rect.width() - 6, PILL_HEIGHT)
                    hits.append(_MonthHit(more, day, hidden_count=hidden))
        return cells, hits

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Window))
        width = self.width() / 7
        painter.setPen(_with_alpha(text_color, 0.6))
        for weekday, name in enumerate(WEEKDAY_SHORT_NAMES):
            painter.drawText(
                QRectF(weekday * width, 0, width, HEADER_HEIGHT), Qt.AlignmentFlag.AlignCenter, name
            )

        cells, hits = self._layout()
        for rect, day in cells:
            in_month = day.month == self._month
            painter.setBrush(
                palette.color(QPalette.ColorRole.Base)
                if in_month
                else _with_alpha(text_color, 0.04)
            )
            is_today = day == self._today
            painter.setPen(
                QPen(_accent(self), 2) if is_today else QPen(_with_alpha(text_color, 0.15), 1)
            )
            painter.drawRoundedRect(rect, 6, 6)
            font = QFont(self.font())
            font.setBold(is_today)
            painter.setFont(font)
            painter.setPen(text_color if in_month else _with_alpha(text_color, 0.45))
            painter.drawText(
                rect.adjusted(6, 2, -4, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                str(day.day),
            )

        painter.setFont(self.font())
        for hit in hits:
            if hit.hidden_count:
                painter.setPen(_accent(self))
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                painter.drawText(hit.rect.adjusted(5, 0, 0, 0), align, f"+{hit.hidden_count} más")
            elif hit.meeting is not None and hit.occurrence is not None:
                inactive = (
                    occurrence_status(hit.meeting, hit.occurrence) is OccurrenceStatus.INACTIVE
                )
                _draw_meeting_box(
                    painter, hit.rect, f"{hit.occurrence:%H:%M} {hit.meeting.title}", inactive, self
                )
        painter.end()

    def _hit_at(self, pos: QPoint) -> _MonthHit | None:
        cells, hits = self._layout()
        point = pos.toPointF()
        for hit in hits:
            if hit.rect.contains(point):
                return hit
        for rect, day in cells:
            if rect.contains(point):
                return _MonthHit(rect, day)
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        hit = self._hit_at(event.position().toPoint())
        if hit is None:
            return
        global_pos = event.globalPosition().toPoint()
        if event.button() is Qt.MouseButton.LeftButton and hit.hidden_count:
            self._callbacks.on_show_day(hit.day)
        elif event.button() is Qt.MouseButton.RightButton:
            if hit.meeting is not None and hit.occurrence is not None:
                self._callbacks.on_meeting_menu(hit.meeting, hit.occurrence, global_pos)
            else:
                self._callbacks.on_empty_menu(
                    default_start_on_day(hit.day, datetime.now()), None, global_pos
                )

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        hit = self._hit_at(event.position().toPoint())
        if hit is not None and hit.meeting is not None:
            self._callbacks.on_meeting_edit(hit.meeting)

    def event(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            hit = self._hit_at(event.pos())
            if hit is not None and hit.meeting is not None and hit.occurrence is not None:
                QToolTip.showText(
                    event.globalPos(), meeting_tooltip(hit.meeting, hit.occurrence), self
                )
            elif hit is not None and hit.hidden_count:
                QToolTip.showText(event.globalPos(), "Ver el día completo", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)
