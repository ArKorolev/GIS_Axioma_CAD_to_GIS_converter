"""Пошаговый мастер конвертации.

Шаги:
1. Выбор файлов DWG/DXF
2. Быстрое сканирование координатных экстентов
3. Настройка параметров (формат, разбивка по слоям, СК)
4. Выбор каталога вывода (по умолчанию — папка исходного файла)
5. Проверка перезаписи существующих файлов
6. Конвертация с прогрессом, логом, паузой/прерыванием
7. Предложение открыть результат в Аксиоме (кроме MIF/MID)
"""

import os

from PySide2.QtWidgets import (
    QMessageBox, QApplication, QDialog, QFileDialog,
)

from axipy import (
    open_files_dialog, Notifications,
    provider_manager, Layer, mainwindow, view_manager,
)

from .converter import ConverterEngine, ConversionResult
from .dialogs import SettingsDialog, ProgressDialog
from .helpers import CAD_FILE_FILTER, file_basename


class ConversionWizard:
    """Управляет пошаговым процессом конвертации."""

    def __init__(self, parent=None):
        self._parent = parent

    def run(self):
        # Шаг 1: Выбор файлов
        files = self._step_select_files()
        if not files:
            return

        # Шаг 2: Быстрое сканирование экстентов (для диалога границ)
        prescan_data = self._scan_extents(files)

        # Шаг 3: Настройка параметров
        settings = self._step_settings(len(files), prescan_data)
        if settings is None:
            return

        # Шаг 4: Выбор каталога
        output_dir = self._step_select_output_dir(
            os.path.dirname(files[0])
        )
        if not output_dir:
            return

        # Шаг 4.5: Проверка доступности каталога для записи
        error = ConverterEngine.check_output_dir_writable(output_dir)
        if error:
            QMessageBox.critical(
                self._parent,
                'Нет доступа к каталогу',
                error,
            )
            return

        # Шаг 5: Конвертация
        results = self._step_convert(
            files, output_dir,
            settings['format'], settings['split'], settings['crs'],
        )

        # Шаг 6: Предложение открыть результат
        if results is not None:
            self._step_offer_open(results, output_dir, settings['format'])

    # ── Шаг 1: Выбор файлов ─────────────────────────────────────
    def _step_select_files(self):
        files = open_files_dialog(CAD_FILE_FILTER)
        if not files:
            return []
        return [str(f) for f in files]

    # ── Шаг 2: Сканирование экстентов ──────────────────────────
    def _scan_extents(self, files):
        """Быстрое сканирование координатных экстентов файлов.

        Возвращает dict или None. Используется диалогом границ
        для кнопки «Подобрать по размеру чертежа».
        """
        try:
            return ConverterEngine.scan_extents(files)
        except Exception:
            return None

    # ── Шаг 3: Настройка параметров ─────────────────────────────
    def _step_settings(self, file_count, prescan_data=None):
        dialog = SettingsDialog(file_count, self._parent, prescan_data)
        if dialog.exec_() != QDialog.Accepted:
            return None
        return {
            'format': dialog.output_format,
            'split': dialog.split_layers,
            'crs': dialog.crs,
        }

    # ── Шаг 4: Выбор каталога ───────────────────────────────────
    def _step_select_output_dir(self, default_dir=None):
        d = QFileDialog.getExistingDirectory(
            self._parent,
            'Выбор каталога для сохранения',
            default_dir or os.path.expanduser('~'),
        )
        if d:
            return str(d)
        return None

    # ── Шаг 5: Конвертация ─────────────────────────────────────
    def _step_convert(self, files, output_dir, output_format,
                      split_layers, crs):
        progress_dialog = ProgressDialog(0, self._parent)
        progress_dialog.show()
        QApplication.processEvents()

        engine = ConverterEngine(
            on_progress=lambda info: progress_dialog.update_progress(info),
            on_log=lambda msg: progress_dialog.log_message(msg),
            on_idle=QApplication.processEvents,
        )
        progress_dialog.set_engine(engine)

        # Предварительное сканирование (с кэшированием слоёв)
        try:
            predictions, total_units, layer_cache = engine.prescan(
                files, output_dir, output_format, split_layers)
        except Exception as e:
            progress_dialog.log_message(
                'Ошибка при сканировании: {}'.format(str(e)))
            progress_dialog.set_complete()
            progress_dialog.exec_()
            progress_dialog.close()
            return None

        progress_dialog.set_total(total_units)

        # Проверка перезаписи
        existing = ConverterEngine.check_existing_files(predictions)
        if existing:
            if not self._confirm_overwrite(existing):
                progress_dialog.log_message(
                    'Конвертация отменена пользователем.')
                progress_dialog.set_complete()
                progress_dialog.exec_()
                progress_dialog.close()
                return None

        # Конвертация (с передачей кэша — без повторного открытия файлов)
        results = engine.convert(
            files=files,
            output_dir=output_dir,
            output_format=output_format,
            split_layers=split_layers,
            crs=crs,
            layer_cache=layer_cache,
            total_units=total_units,
        )

        progress_dialog.set_complete()
        # Ждём, пока пользователь прочитает лог и нажмёт «Закрыть»
        progress_dialog.exec_()
        progress_dialog.close()
        return results

    # ── Проверка перезаписи ─────────────────────────────────────
    def _confirm_overwrite(self, existing_files):
        unique_names = sorted(set(os.path.basename(f) for f in existing_files))

        msg = 'В папке назначения уже существуют файлы ' \
              'с совпадающими именами:\n\n'
        for name in unique_names[:20]:
            msg += '  • {}\n'.format(name)
        if len(unique_names) > 20:
            msg += '  …и ещё {} файл(ов)\n'.format(len(unique_names) - 20)
        msg += '\nОни будут перезаписаны. Продолжить?'

        reply = QMessageBox.question(
            self._parent,
            'Перезапись файлов',
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    # ── Шаг 6: Предложение открыть результат ────────────────────
    def _step_offer_open(self, results, output_dir, output_format):
        success_results = [r for r in results if r.success]
        errors = [r for r in results if not r.success]
        aborted = any(
            r.error == 'Прервано пользователем' for r in results
        )

        if aborted:
            QMessageBox.information(
                self._parent,
                'Конвертация прервана',
                'Конвертация прервана пользователем.\n\n'
                'Успешно: {} файл(ов).'.format(len(success_results)),
            )
            return

        if errors:
            error_lines = [
                '  • {}: {}'.format(file_basename(r.source_file), r.error)
                for r in errors[:10]
            ]
            QMessageBox.warning(
                self._parent,
                'Конвертация завершена с ошибками',
                'Успешно: {} файл(ов).\n\n'
                'Ошибки:\n{}'.format(
                    len(success_results), '\n'.join(error_lines)
                ),
            )

        if not success_results:
            return

        all_output_files = []
        for r in success_results:
            all_output_files.extend(r.output_files)

        total = len(all_output_files)

        if output_format == 'mif':
            QMessageBox.information(
                self._parent,
                'Конвертация завершена',
                'Сконвертировано {} файл(ов) в MIF/MID.\n\n'
                'Файлы сохранены в: {}'.format(total, output_dir),
            )
            Notifications.push(
                'Конвертер САПР → ГИС',
                'Сконвертировано {} файл(ов) в MIF/MID'.format(total),
            )
            return

        reply = QMessageBox.question(
            self._parent,
            'Конвертация завершена',
            'Сконвертировано {} файл(ов).\n\n'
            'Открыть результат в Аксиоме?'.format(total),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if reply == QMessageBox.Yes:
            self._open_in_axioma(all_output_files)

        Notifications.push(
            'Конвертер САПР → ГИС',
            'Сконвертировано {} файл(ов)'.format(total),
        )

    # ── Открытие результатов в Аксиоме ──────────────────────────
    def _open_in_axioma(self, filepaths):
        layers = []
        for filepath in filepaths:
            try:
                table = provider_manager.openfile(filepath)
                layer = Layer.create(table)
                layers.append(layer)
            except Exception:
                pass

        if not layers:
            QMessageBox.warning(
                self._parent,
                'Открытие результатов',
                'Не удалось открыть ни один файл.',
            )
            return

        try:
            if view_manager.mapviews:
                for layer in layers:
                    mainwindow.add_layer_current_map(layer)
            else:
                mainwindow.add_layer_new_map(layers[0])
                for layer in layers[1:]:
                    mainwindow.add_layer_current_map(layer)
        except Exception as e:
            QMessageBox.warning(
                self._parent,
                'Открытие результатов',
                'Ошибка при открытии: {}'.format(str(e)),
            )
