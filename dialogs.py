"""Диалоги для настройки параметров и отображения прогресса."""

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QPushButton, QProgressBar, QGroupBox,
    QPlainTextEdit, QApplication, QLineEdit, QMessageBox,
)

from axipy import CoordSystem, ChooseCoordSystemDialog, Rect, Unit

from .helpers import (
    OUTPUT_FORMATS, format_number, get_precision_limits,
    calc_snap_step_str, get_unit_str,
)


def _create_default_crs():
    """Создаёт СК «План-схема, метры» с границами из PRECISION_LIMITS."""
    try:
        limits = get_precision_limits('m')
        bound = limits['max_bound']
        return CoordSystem.from_units(
            Unit.m, Rect(-bound, -bound, bound, bound)
        )
    except Exception:
        try:
            return CoordSystem.from_prj("NonEarth 0, 'm'")
        except Exception:
            return None


def _ensure_wide_bounds(crs):
    """Для NonEarth СК устанавливает границы по лимитам точности единицы."""
    if crs is None:
        return crs
    try:
        if crs.non_earth:
            unit_str = get_unit_str(crs.unit)
            limits = get_precision_limits(unit_str)
            bound = limits['max_bound']
            return CoordSystem.from_units(
                crs.unit, Rect(-bound, -bound, bound, bound)
            )
    except Exception:
        pass
    return crs


class PlanCoordDialog(QDialog):
    """Диалог «Координатная система плана» с автозаполнением и валидацией."""

    def __init__(self, parent, unit_str='m', prescan_data=None):
        super().__init__(parent)
        self._unit_str = unit_str
        self._prescan_data = prescan_data
        self._result = None

        self.setWindowTitle(
            'Координатная система плана ({})'.format(unit_str)
        )
        self.setMinimumWidth(480)
        self.setModal(True)

        self._build_ui()
        self._auto_fill()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # ── Поля ввода ──
        grid = QGridLayout()
        grid.setSpacing(8)

        labels = ['Мин X:', 'Макс X:', 'Мин Y:', 'Макс Y:']
        self._entries = []

        for i, label_text in enumerate(labels):
            lbl = QLabel(label_text)
            grid.addWidget(lbl, i, 0)

            entry = QLineEdit()
            entry.setPlaceholderText('0.00')
            grid.addWidget(entry, i, 1)

            self._entries.append(entry)

        unit_lbl = QLabel('({})'.format(self._unit_str))
        unit_lbl.setStyleSheet('color: gray; font-weight: bold;')
        grid.addWidget(unit_lbl, 0, 2, 4, 1)

        layout.addLayout(grid)

        # ── Кнопки «Подобрать» и «Установить рекомендуемые» ──
        btn_row = QHBoxLayout()
        self.btn_auto = QPushButton('Подобрать по размеру чертежа')
        self.btn_auto.clicked.connect(self._on_auto_fit)
        btn_row.addWidget(self.btn_auto)

        self.btn_recommended = QPushButton('Установить рекомендуемые')
        self.btn_recommended.clicked.connect(self._on_set_recommended)
        btn_row.addWidget(self.btn_recommended)

        layout.addLayout(btn_row)

        # ── Информационная плашка ──
        limits = get_precision_limits(self._unit_str)
        info_text = (
            'Рекомендовано для {}: до {} (шаг {}).\n'
            'Большие значения увеличивают шаг и снижают точность.'.format(
                self._unit_str,
                format_number(limits['max_bound']),
                limits['step_str']
            )
        )
        self._info_lbl = QLabel(info_text)
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet(
            'color: gray; font-size: 11px; '
            'background-color: #f5f5f5; '
            'border: 1px solid #ddd; '
            'border-radius: 4px; padding: 6px;'
        )
        layout.addWidget(self._info_lbl)

        # ── Кнопки OK / Отмена ──
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_ok = QPushButton('OK')
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._on_ok)

        self.btn_cancel = QPushButton('Отмена')
        self.btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(self.btn_ok)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)

    def _auto_fill(self):
        """Заполняет поля рекомендуемыми границами для выбранной единицы."""
        limits = get_precision_limits(self._unit_str)
        bound = limits['max_bound']
        self._set_bounds(bound)

    def _on_set_recommended(self):
        """Кнопка «Установить рекомендуемые» — сбрасывает к значениям из таблицы."""
        limits = get_precision_limits(self._unit_str)
        bound = limits['max_bound']
        self._set_bounds(bound)

    def _set_bounds(self, bound):
        """Устанавливает симметричные границы во все поля."""
        self._entries[0].setText(format_number(-bound))
        self._entries[1].setText(format_number(bound))
        self._entries[2].setText(format_number(-bound))
        self._entries[3].setText(format_number(bound))

    def _on_auto_fit(self):
        """Обработка кнопки «Подобрать по размеру чертежа»."""
        if not self._prescan_data:
            QMessageBox.warning(
                self, 'Нет данных',
                'Данные о размере чертежа недоступны.\n'
                'Введите границы вручную или установите рекомендуемые.'
            )
            return

        min_x = self._prescan_data.get('min_x')
        max_x = self._prescan_data.get('max_x')
        min_y = self._prescan_data.get('min_y')
        max_y = self._prescan_data.get('max_y')

        if None in (min_x, max_x, min_y, max_y):
            QMessageBox.critical(
                self, 'Ошибка',
                'Не удалось получить координаты из прескана.'
            )
            return

        margin = 1.1
        auto_bound = max(
            abs(min_x), abs(max_x), abs(min_y), abs(max_y)
        ) * margin

        limits = get_precision_limits(self._unit_str)

        if auto_bound > limits['max_bound']:
            step_str = calc_snap_step_str(auto_bound, self._unit_str)
            reply = QMessageBox.question(
                self, 'Предупреждение',
                'Автоподбор выходит за пределы рекомендуемой точности.\n'
                'Шаг привязки станет {}.\n'
                'Применить эти значения?'.format(step_str),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        self._set_bounds(auto_bound)

    def _on_ok(self):
        """Валидация и подтверждение ввода."""
        try:
            vals = []
            for entry in self._entries:
                text = entry.text().replace(' ', '').strip()
                if not text:
                    raise ValueError('Все поля должны быть заполнены.')
                vals.append(float(text))

            min_x, max_x, min_y, max_y = vals

            if min_x >= max_x:
                raise ValueError('Мин. X должен быть меньше макс. X.')
            if min_y >= max_y:
                raise ValueError('Мин. Y должен быть меньше макс. Y.')

            bound = max(abs(min_x), abs(max_x), abs(min_y), abs(max_y))
            limits = get_precision_limits(self._unit_str)

            if bound > limits['max_bound']:
                step_str = calc_snap_step_str(bound, self._unit_str)
                reply = QMessageBox.question(
                    self, 'Низкая точность',
                    'Указанный диапазон приведёт к шагу привязки {}.\n'
                    'Это может быть недостаточно точно для кадастровых '
                    'данных. Продолжить?'.format(step_str),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    return

            self._result = {
                'min_x': min_x, 'max_x': max_x,
                'min_y': min_y, 'max_y': max_y,
            }
            self.accept()

        except ValueError as e:
            QMessageBox.critical(
                self, 'Ошибка ввода',
                'Некорректные значения:\n{}'.format(str(e))
            )

    def get_result(self):
        """Возвращает словарь с границами или None."""
        return self._result


class SettingsDialog(QDialog):
    """Диалог выбора формата и параметров конвертации."""

    def __init__(self, file_count, parent=None, prescan_data=None):
        super().__init__(parent)
        self.setWindowTitle('Параметры конвертации САПР → ГИС')
        self.setMinimumWidth(420)
        self._file_count = file_count
        self._crs = _create_default_crs()
        self._prescan_data = prescan_data
        self._build_ui()
        self._update_crs_label()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl_info = QLabel('Выбрано файлов: {}'.format(self._file_count))
        lbl_info.setStyleSheet('font-weight: bold;')
        layout.addWidget(lbl_info)

        grp = QGroupBox('Формат выходных файлов')
        grp_layout = QVBoxLayout()
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel('Формат:'))
        self.combo_format = QComboBox()
        for label in OUTPUT_FORMATS:
            self.combo_format.addItem(label)
        fmt_row.addWidget(self.combo_format)
        grp_layout.addLayout(fmt_row)
        grp.setLayout(grp_layout)
        layout.addWidget(grp)

        grp_crs = QGroupBox('Система координат')
        crs_layout = QVBoxLayout()

        crs_row = QHBoxLayout()
        self.lbl_crs = QLabel()
        self.lbl_crs.setWordWrap(True)
        crs_row.addWidget(self.lbl_crs, 1)

        self.btn_crs = QPushButton('Выбрать…')
        self.btn_crs.clicked.connect(self._choose_crs)
        crs_row.addWidget(self.btn_crs)
        crs_layout.addLayout(crs_row)

        if self._file_count > 1:
            lbl_warn = QLabel(
                'Выбранная система координат будет применена '
                'ко всем файлам. Убедитесь, что все исходные '
                'файлы в одной системе координат.'
            )
            lbl_warn.setWordWrap(True)
            lbl_warn.setStyleSheet(
                'color: #b8860b; font-size: 11px; '
                'background-color: #fffbe6; '
                'border: 1px solid #e8d44d; '
                'border-radius: 4px; padding: 6px;'
            )
            crs_layout.addWidget(lbl_warn)

        grp_crs.setLayout(crs_layout)
        layout.addWidget(grp_crs)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_ok = QPushButton('Далее')
        self.btn_ok.setDefault(True)
        self.btn_cancel = QPushButton('Отмена')
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_ok)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)

        self.btn_ok.clicked.connect(self.accept)

    def _choose_crs(self):
        dialog = ChooseCoordSystemDialog(self._crs)
        if dialog.exec_() == QDialog.Accepted:
            chosen = dialog.chosenCoordSystem()
            if chosen is not None:
                if self._is_flat_crs(chosen):
                    self._show_bounds_dialog(chosen)
                else:
                    self._crs = chosen
                    self._update_crs_label()

    def _is_flat_crs(self, crs):
        """Проверяет, является ли СК плоской (NonEarth)."""
        try:
            return crs.non_earth
        except Exception:
            return False

    def _show_bounds_dialog(self, chosen_crs):
        """Открывает диалог «Координатная система плана»."""
        unit_str = get_unit_str(chosen_crs.unit)
        bounds_dialog = PlanCoordDialog(
            self, unit_str, self._prescan_data
        )
        if bounds_dialog.exec_() == QDialog.Accepted:
            result = bounds_dialog.get_result()
            if result:
                try:
                    self._crs = CoordSystem.from_units(
                        chosen_crs.unit,
                        Rect(
                            result['min_x'], result['min_y'],
                            result['max_x'], result['max_y']
                        )
                    )
                    self._update_crs_label()
                except Exception as e:
                    self._crs = _ensure_wide_bounds(chosen_crs)
                    self._update_crs_label()
                    QMessageBox.warning(
                        self, 'Внимание',
                        'Не удалось создать СК с заданными границами:\n'
                        '{}\n\n'
                        'Применены стандартные границы для '
                        'единицы «{}».'.format(str(e), unit_str)
                    )

    def _update_crs_label(self):
        if self._crs is not None:
            try:
                title = self._crs.title
            except Exception:
                title = str(self._crs)
            self.lbl_crs.setText(title)
        else:
            self.lbl_crs.setText('Не задана (по умолчанию)')

    @property
    def output_format(self):
        return OUTPUT_FORMATS[self.combo_format.currentText()]

    @property
    def crs(self):
        return self._crs


class ProgressDialog(QDialog):
    """Диалог с прогресс-баром, логом и кнопками пауза/прервать."""

    def __init__(self, total_units, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Конвертация…')
        self.setMinimumWidth(600)
        self.setMinimumHeight(420)
        self.setModal(True)
        self._total = total_units
        self._completed = False
        self._engine = None
        self._build_ui()

    def set_engine(self, engine):
        self._engine = engine

    def set_total(self, total):
        self._total = total
        self.progress.setRange(0, total)
        self.progress.setValue(0)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.label_file = QLabel('')
        self.label_file.setStyleSheet('font-weight: bold;')
        layout.addWidget(self.label_file)

        self.label_status = QLabel('Подготовка…')
        layout.addWidget(self.label_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, self._total)
        self.progress.setValue(0)
        self.progress.setFormat('%v / %m (%p%)')
        layout.addWidget(self.progress)

        layout.addWidget(QLabel('Лог:'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        layout.addWidget(self.log, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_pause = QPushButton('Пауза')
        self.btn_pause.setMinimumWidth(100)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_abort = QPushButton('Прервать')
        self.btn_abort.setMinimumWidth(100)
        self.btn_abort.setStyleSheet('QPushButton { color: #cc0000; }')
        self.btn_abort.clicked.connect(self._abort)
        self.btn_close = QPushButton('Закрыть')
        self.btn_close.setMinimumWidth(100)
        self.btn_close.setDefault(True)
        self.btn_close.hide()
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_pause)
        btn_row.addWidget(self.btn_abort)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

    def update_progress(self, info):
        if info.current >= 0:
            self.progress.setValue(info.current)
        if info.current_name:
            self.label_file.setText(info.current_name)
        if info.stage:
            if info.current_name:
                self.label_status.setText(
                    '[{}/{}] {}'.format(info.current, info.total, info.stage)
                )
            else:
                self.label_status.setText(info.stage)
        QApplication.processEvents()

    def log_message(self, message):
        self.log.appendPlainText(message)
        QApplication.processEvents()

    def _toggle_pause(self):
        if self._engine is None:
            return
        if self.btn_pause.text() == 'Пауза':
            self._engine.set_paused(True)
            self.btn_pause.setText('Продолжить')
            self.label_status.setText('Пауза')
        else:
            self._engine.set_paused(False)
            self.btn_pause.setText('Пауза')

    def _abort(self):
        if self._engine is not None:
            self._engine.abort()
        self.btn_abort.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.label_status.setText('Прерывание…')
        QApplication.processEvents()

    def closeEvent(self, event):
        if self._completed:
            event.accept()
        else:
            self._abort()
            event.ignore()

    def set_complete(self):
        self._completed = True
        self.progress.setValue(self._total)
        self.btn_pause.setEnabled(False)
        self.btn_abort.setEnabled(False)
        self.btn_pause.hide()
        self.btn_abort.hide()
        self.btn_close.show()
        self.btn_close.setFocus()
        self.label_status.setText('Конвертация завершена')
        QApplication.processEvents()
