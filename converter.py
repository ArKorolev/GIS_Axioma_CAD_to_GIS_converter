"""Движок конвертации DWG/DXF в ГИС-форматы."""

import os
import time
import uuid
import shutil
import tempfile
import gc
import traceback as _tb
from dataclasses import dataclass
from typing import List, Optional, Callable, Dict

from axipy import provider_manager, Schema, Attribute

from .helpers import sanitize_filename, file_basename


class ConverterAborted(Exception):
    """Конвертация прервана пользователем."""
    pass


@dataclass
class ConversionResult:
    source_file: str
    output_files: List[str]
    error: Optional[str] = None

    @property
    def success(self):
        return self.error is None and len(self.output_files) > 0


@dataclass
class ProgressInfo:
    current: int
    total: int
    current_name: str
    stage: str


class ConverterEngine:

    def __init__(self, on_progress=None, on_log=None, on_idle=None):
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_idle = on_idle
        self._aborted = False
        self._paused = False
        self._current_unit = 0
        self._total_units = 0
        self._current_name = ''

    # ── Управление ──

    def abort(self):
        self._aborted = True

    def set_paused(self, paused):
        self._paused = paused

    @property
    def is_aborted(self):
        return self._aborted

    def _check_flags(self):
        while self._paused and not self._aborted:
            if self._on_idle is not None:
                self._on_idle()
            time.sleep(0.05)
        if self._aborted:
            raise ConverterAborted()

    def _log(self, message):
        if self._on_log is not None:
            self._on_log(message)

    def _notify(self, stage, name=''):
        """Показывает текущий этап без инкремента счётчика."""
        self._current_name = name or self._current_name
        if self._on_progress is not None:
            self._on_progress(ProgressInfo(
                current=self._current_unit,
                total=self._total_units,
                current_name=self._current_name,
                stage=stage,
            ))

    def _advance(self):
        """Инкремент счётчика после завершения единицы обработки."""
        self._current_unit += 1
        if self._on_progress is not None:
            self._on_progress(ProgressInfo(
                current=self._current_unit,
                total=self._total_units,
                current_name=self._current_name,
                stage='Готово',
            ))

    # ── Безопасное открытие файла ──

    @staticmethod
    def _safe_open(filepath):
        """Открывает файл напрямую.

        Кириллица в пути не мешает провайдеру DWG.
        Возвращает (data_object, None).
        """
        return provider_manager.open({'src': filepath}), None

    @staticmethod
    def _safe_schema(data_object):
        """Читает schema из data_object. При ошибке возвращает None.

        None означает, что схему нужно строить из элементов.
        """
        try:
            schema = data_object.schema
            names = list(schema.attribute_names)
            if names:
                return schema
            return None
        except Exception:
            return None

    @staticmethod
    def _build_schema_from_features(features, data_object):
        """Строит Schema из списка Feature-объектов.

        Все атрибуты — string (совместимо с любыми значениями).
        Служебные ключи (+geometry, +style) пропускаются.
        Добавляет coordsystem из data_object — без него геометрия теряется.
        """
        schema = Schema()

        attr_names = []
        seen = set()
        for feat in features:
            for key in feat.keys():
                if key.startswith('+'):
                    continue
                if key not in seen:
                    seen.add(key)
                    attr_names.append(key)

        for name in attr_names:
            schema.append(Attribute(name, 'string'))

        try:
            schema.coordsystem = data_object.coordsystem
        except Exception:
            pass

        return schema

    # ── Предварительное сканирование ──

    def prescan(self, files, output_dir, output_format):
        """Подсчитывает единицы обработки и предсказывает пути вывода.

        Всегда разбивает по слоям.
        Возвращает (predictions, total_units, layer_cache).
        layer_cache — словарь {filepath: [layer_names] или None}.
        """
        predictions = []
        total_units = 0
        layer_cache = {}

        for filepath in files:
            self._check_flags()
            basename = file_basename(filepath)
            self._log('Сканирование: {}…'.format(basename))
            temp_path = None
            try:
                data_object, temp_path = ConverterEngine._safe_open(filepath)
                layers = data_object.layers
                if layers:
                    layer_cache[filepath] = list(layers)
                    output_paths = []
                    for layer_name in layers:
                        safe_name = '{}_{}'.format(
                            basename, sanitize_filename(layer_name))
                        out_path = os.path.join(
                            output_dir,
                            '{}.{}'.format(safe_name, output_format))
                        output_paths.append(out_path)
                    count = len(layers)
                else:
                    layer_cache[filepath] = None
                    out_path = os.path.join(
                        output_dir,
                        '{}.{}'.format(basename, output_format))
                    output_paths = [out_path]
                    count = 1
                data_object.close()
                if temp_path is not None:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            except Exception:
                if temp_path is not None:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                layer_cache[filepath] = None
                out_path = os.path.join(
                    output_dir,
                    '{}.{}'.format(basename, output_format))
                output_paths = [out_path]
                count = 1

            predictions.append((filepath, output_paths))
            total_units += count

        return predictions, total_units, layer_cache

    # ── Быстрое сканирование экстентов ──

    @staticmethod
    def scan_extents(files):
        """Быстрое сканирование координатных экстентов файлов.

        Читает экстент из coordsystem.rect — мгновенно, без перебора геометрий.
        Возвращает dict с ключами min_x, max_x, min_y, max_y — или None.
        """
        result = {'min_x': None, 'max_x': None,
                  'min_y': None, 'max_y': None}

        for filepath in files:
            extent = None

            # Путь 1: _safe_open → coordsystem.rect
            if extent is None:
                temp_path = None
                try:
                    data_object, temp_path = ConverterEngine._safe_open(filepath)
                    try:
                        cs = data_object.coordsystem
                        if cs is not None:
                            rect = cs.rect
                            if rect is not None:
                                extent = ConverterEngine._rect_to_tuple(rect)
                    finally:
                        try:
                            data_object.close()
                        except Exception:
                            pass
                        if temp_path is not None:
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
                except Exception:
                    if temp_path is not None:
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    pass

            # Путь 2: provider_manager.openfile → coordsystem.rect
            if extent is None:
                try:
                    table = provider_manager.openfile(filepath)
                    try:
                        cs = table.coordsystem
                        if cs is not None:
                            rect = cs.rect
                            if rect is not None:
                                extent = ConverterEngine._rect_to_tuple(rect)
                    finally:
                        try:
                            table.close()
                        except Exception:
                            pass
                except Exception:
                    pass

            # Записываем результат
            if extent is not None:
                min_x, max_x, min_y, max_y = extent
                if result['min_x'] is None or min_x < result['min_x']:
                    result['min_x'] = min_x
                if result['max_x'] is None or max_x > result['max_x']:
                    result['max_x'] = max_x
                if result['min_y'] is None or min_y < result['min_y']:
                    result['min_y'] = min_y
                if result['max_y'] is None or max_y > result['max_y']:
                    result['max_y'] = max_y

        if result['min_x'] is None:
            return None
        return result

    @staticmethod
    def _try_extent_from_obj(obj):
        """Пробует получить экстент из объекта через различные свойства.

        Возвращает (xmin, xmax, ymin, ymax) или None.
        """
        for attr_name in ('extent', 'bounds', 'rect', 'position', 'domain',
                         'spatial_extent', 'data_extent'):
            try:
                val = getattr(obj, attr_name)
                if val is not None:
                    rect = ConverterEngine._rect_to_tuple(val)
                    if rect is not None:
                        return rect
            except Exception:
                continue

        for method_name in ('extent', 'bounds', 'rect', 'get_extent',
                           'getExtent', 'get_bounds'):
            try:
                method = getattr(obj, method_name)
                if callable(method):
                    val = method()
                    if val is not None:
                        rect = ConverterEngine._rect_to_tuple(val)
                        if rect is not None:
                            return rect
            except Exception:
                continue

        return None

    @staticmethod
    def _rect_to_tuple(val):
        """Преобразует Rect axipy в кортеж (xmin, xmax, ymin, ymax)."""
        for pat in [
            ('xmin', 'xmax', 'ymin', 'ymax'),
            ('x_min', 'x_max', 'y_min', 'y_max'),
            ('x0', 'x1', 'y0', 'y1'),
            ('left', 'right', 'bottom', 'top'),
        ]:
            try:
                return (
                    float(getattr(val, pat[0])),
                    float(getattr(val, pat[1])),
                    float(getattr(val, pat[2])),
                    float(getattr(val, pat[3])),
                )
            except (AttributeError, TypeError, ValueError):
                continue

        try:
            if hasattr(val, '__len__') and len(val) >= 4:
                return (
                    float(val[0]),
                    float(val[2]),
                    float(val[1]),
                    float(val[3]),
                )
        except (TypeError, ValueError, IndexError):
            pass

        try:
            if hasattr(val, '__len__') and len(val) == 2:
                p0, p1 = val[0], val[1]
                return (
                    float(p0.x), float(p1.x),
                    float(p0.y), float(p1.y),
                )
        except (TypeError, ValueError, IndexError, AttributeError):
            pass

        try:
            import re
            s = str(val)
            m = re.findall(r'[-\d.]+', s)
            if len(m) >= 4:
                return (
                    float(m[0]), float(m[2]),
                    float(m[1]), float(m[3]),
                )
        except (TypeError, ValueError):
            pass

        return None

    @staticmethod
    def check_existing_files(predictions):
        """Возвращает список существующих выходных файлов."""
        existing = []
        for _, output_paths in predictions:
            for out_path in output_paths:
                if os.path.exists(out_path):
                    existing.append(out_path)
        return existing

    # ── Временные файлы ──

    @staticmethod
    def _get_temp_base(name_hint='cad2gis'):
        temp_dir = tempfile.gettempdir()
        return os.path.join(temp_dir, '{}_{}'.format(
            sanitize_filename(name_hint), uuid.uuid4().hex))

    @staticmethod
    def _cleanup_tab_files(tab_path):
        base = tab_path.rsplit('.', 1)[0]
        for ext in ('.tab', '.dat', '.id', '.map', '.ind'):
            path = base + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    @staticmethod
    def _cleanup_mif_files(mif_path):
        base = mif_path.rsplit('.', 1)[0]
        for ext in ('.mif', '.mid'):
            path = base + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    @staticmethod
    def _move_tab_files(src_tab_path, dst_tab_path):
        src_base = src_tab_path.rsplit('.', 1)[0]
        dst_base = dst_tab_path.rsplit('.', 1)[0]
        for ext in ('.tab', '.dat', '.id', '.map', '.ind'):
            src = src_base + ext
            dst = dst_base + ext
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)

    # ── Проверка выходного файла ──

    @staticmethod
    def _ensure_output_writable(out_path, output_format):
        if output_format == 'tab':
            base = out_path.rsplit('.', 1)[0]
            for ext in ('.tab', '.dat', '.id', '.map', '.ind'):
                path = base + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        raise RuntimeError(
                            'Не удалось перезаписать {} — '
                            'файл открыт в другой программе'.format(
                                os.path.basename(path)))
        else:
            base = out_path.rsplit('.', 1)[0]
            for ext in ('.mif', '.mid', '.shp', '.shx', '.dbf', '.prj'):
                path = base + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        raise RuntimeError(
                            'Не удалось перезаписать {} — '
                            'файл открыт в другой программе'.format(
                                os.path.basename(path)))

    @staticmethod
    def check_output_dir_writable(output_dir):
        """Проверяет, доступен ли каталог для записи.

        Возвращает None если ОК, либо сообщение об ошибке.
        """
        if not os.path.isdir(output_dir):
            return 'Каталог не существует: {}'.format(output_dir)
        test_file = os.path.join(output_dir, '.cad2gis_write_test')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except Exception as e:
            return 'Нет прав на запись в каталог: {}'.format(str(e))
        return None

    # ── CRS ──

    @staticmethod
    def _get_prj_string(crs):
        try:
            prj = crs.prj
            if not prj:
                return None
            if crs.non_earth and 'bounds' not in prj.lower():
                try:
                    rect = crs.rect
                    prj = '{} Bounds ({}, {}) ({}, {})'.format(
                        prj.strip(),
                        int(rect.xmin), int(rect.ymin),
                        int(rect.xmax), int(rect.ymax)
                    )
                except Exception:
                    pass
            return prj
        except Exception:
            return None

    def _replace_crs_in_mif_bytes(self, mif_path, crs):
        prj = self._get_prj_string(crs)
        if prj is None:
            return False

        new_line = 'CoordSys ' + prj

        try:
            with open(mif_path, 'rb') as f:
                raw = f.read()
        except Exception:
            return False

        lines = raw.split(b'\n')
        replaced = False

        for i, line in enumerate(lines):
            text = line.decode('utf-8', errors='replace').strip()
            if text.lower().startswith('coordsys'):
                lines[i] = new_line.encode('utf-8')
                replaced = True
                break

        if not replaced:
            for i, line in enumerate(lines):
                text = line.decode('utf-8', errors='replace').strip()
                if text.lower().startswith('data') or text == '':
                    lines.insert(i, new_line.encode('utf-8'))
                    replaced = True
                    break

        if replaced:
            with open(mif_path, 'wb') as f:
                f.write(b'\n'.join(lines))
            return True
        return False

    def _write_prj_for_shp(self, shp_path, crs):
        prj_path = shp_path.rsplit('.', 1)[0] + '.prj'
        wkt = ''
        try:
            wkt = crs.wkt
        except Exception:
            wkt = ''
        if wkt:
            with open(prj_path, 'w', encoding='utf-8') as f:
                f.write(wkt)
        else:
            prj = self._get_prj_string(crs)
            if prj:
                with open(prj_path, 'w', encoding='utf-8') as f:
                    f.write(prj)

    # ── Основная логика ──

    def convert(self, files, output_dir, output_format,
                crs=None, layer_cache=None, total_units=None):
        """Конвертация файлов.

        Всегда разбивает по слоям.
        Если переданы layer_cache и total_units (из prescan),
        прескан внутри не выполняется — экономия на повторном открытии файлов.
        """
        if total_units is None:
            _, total_units, layer_cache = self.prescan(
                files, output_dir, output_format)

        self._total_units = total_units
        self._current_unit = 0

        results = []
        t_start = time.time()

        for i, filepath in enumerate(files):
            try:
                self._check_flags()
            except ConverterAborted:
                self._log('━━━ Конвертация прервана ━━━')
                for fp in files[i:]:
                    results.append(ConversionResult(
                        source_file=fp,
                        output_files=[],
                        error='Прервано пользователем',
                    ))
                break

            basename = file_basename(filepath)
            self._current_name = basename
            self._log('')
            self._log('Файл: {}'.format(basename))

            try:
                result = self._convert_one(
                    filepath, output_dir, output_format,
                    basename, crs,
                    cached_layers=layer_cache.get(filepath) if layer_cache else None,
                )
            except ConverterAborted:
                self._log('  Прервано пользователем')
                results.append(ConversionResult(
                    source_file=filepath,
                    output_files=[],
                    error='Прервано пользователем',
                ))
                for fp in files[i + 1:]:
                    results.append(ConversionResult(
                        source_file=fp,
                        output_files=[],
                        error='Прервано пользователем',
                    ))
                self._log('━━━ Конвертация прервана ━━━')
                break
            except Exception as e:
                tb = _tb.format_exc()
                result = ConversionResult(
                    source_file=filepath,
                    output_files=[],
                    error=str(e),
                )
                self._log('  Ошибка: {}'.format(str(e)))
                self._log('  Traceback:')
                for line in tb.splitlines():
                    self._log('    {}'.format(line))

            results.append(result)
            if result.success:
                self._log('  Готово: {} файл(ов)'.format(
                    len(result.output_files)))

            if self._aborted:
                for fp in files[i + 1:]:
                    results.append(ConversionResult(
                        source_file=fp,
                        output_files=[],
                        error='Прервано пользователем',
                    ))
                self._log('━━━ Конвертация прервана ━━━')
                break

        # ── Сводка ──
        total_elapsed = time.time() - t_start
        total_files = len(files)
        total_output = sum(len(r.output_files) for r in results if r.success)
        error_count = sum(1 for r in results if not r.success)

        self._log('')
        self._log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self._log('Файлов обработано: {}'.format(total_files))
        self._log('Файлов создано: {}'.format(total_output))
        if error_count:
            self._log('Ошибок: {}'.format(error_count))
        self._log('Общее время: {:.1f} с'.format(total_elapsed))

        return results

    # ── Конвертация одного файла ──

    def _convert_one(self, filepath, output_dir, output_format,
                     basename, crs=None, cached_layers=None):
        """Конвертация одного файла. Всегда разбивает по слоям.

        Безопасный путь: перебор по слоям без загрузки всех данных в память.
        Нативная схема получается после первого items(layer_name).

        Возвращает ConversionResult.
        """
        data_object = provider_manager.open({'src': filepath})
        output_files = []

        try:
            output_files = self._convert_layers_safe(
                data_object, output_dir, output_format,
                basename, crs)
        finally:
            data_object.close()

        return ConversionResult(
            source_file=filepath,
            output_files=output_files,
        )

    def _convert_layers_safe(self, data_object, output_dir, output_format,
                             basename, crs):
        """Безопасный путь: перебор по слоям, низкое потребление памяти.

        data_object.layers доступен без items().
        Для каждого слоя — items(layer_name) + export().
        Нативная схема получается после первого items(layer_name).

        Возвращает список созданных файлов.
        """
        output_files = []
        native_schema = None

        for layer_name in data_object.layers:
            try:
                self._check_flags()
            except ConverterAborted:
                raise

            # Читаем объекты текущего слоя
            try:
                items_iter = data_object.items(layer_name)
                first = next(items_iter, None)
                if first is None:
                    continue
            except Exception as e:
                self._log('  Слой "{}": ошибка чтения — {}'.format(
                    layer_name, e))
                continue

            # После первого items() нативная схема становится доступной
            if native_schema is None:
                try:
                    native_schema = data_object.schema
                    self._log('  Нативная схема: {} атрибут(ов)'.format(
                        len(list(native_schema.attribute_names))))
                except Exception:
                    self._log('  Нативная схема недоступна, '
                              'используем string-схему')

            # Схема: нативная или fallback на string
            if native_schema is not None:
                schema = native_schema
            else:
                schema = self._build_schema_from_features(
                    [first], data_object)

            safe_name = '{}_{}'.format(basename, sanitize_filename(layer_name))
            out_path = os.path.join(
                output_dir,
                '{}.{}'.format(safe_name, output_format))

            self._notify('Конвертация: {}'.format(layer_name), safe_name)

            # Переоткрываем итератор (первый next его сдвинул)
            items_iter = data_object.items(layer_name)

            try:
                self._ensure_output_writable(out_path, output_format)
                self._export(items_iter, out_path, output_format,
                             schema, safe_name, crs)
                output_files.append(out_path)
                self._log('  Слой "{}": OK'.format(layer_name))
            except Exception as e:
                self._log('  Слой "{}": ошибка — {}'.format(layer_name, e))

            self._advance()

        return output_files

    # ── Экспорт ──

    def _export(self, items, out_path, output_format,
                src_schema, name_hint, crs=None):
        """Экспорт items в финальный формат через промежуточный TAB."""
        temp_tab_path = self._get_temp_base(
            sanitize_filename(name_hint)) + '.tab'

        try:
            if src_schema is None:
                src_schema = Schema()
            tab_dest = self._get_destination(temp_tab_path, 'tab', src_schema)
            tab_dest.export(items)
            try:
                if hasattr(tab_dest, 'close'):
                    tab_dest.close()
            except Exception:
                pass
            del tab_dest
            gc.collect()

            if output_format == 'tab' and crs is not None:
                self._tab_via_mif(temp_tab_path, out_path, crs)
            elif output_format == 'tab':
                self._move_tab_files(temp_tab_path, out_path)
            elif output_format == 'shp':
                self._tab_to_shp(temp_tab_path, out_path)
                if crs is not None:
                    self._write_prj_for_shp(out_path, crs)
            elif output_format == 'mif':
                self._tab_to_mif(temp_tab_path, out_path)
                if crs is not None:
                    self._replace_crs_in_mif_bytes(out_path, crs)
            else:
                raise ValueError('Неподдерживаемый формат: {}'.format(
                    output_format))
        finally:
            self._cleanup_tab_files(temp_tab_path)

    def _tab_via_mif(self, temp_tab_path, final_tab_path, crs):
        """Перезаписывает CRS в TAB-файле через промежуточный MIF."""
        temp_mif_path = self._get_temp_base('crs_fix') + '.mif'

        try:
            tab_obj = provider_manager.open({'src': temp_tab_path})
            try:
                mif_dest = self._get_destination(
                    temp_mif_path, 'mif', tab_obj.schema)
                mif_dest.export(tab_obj.items())
                try:
                    if hasattr(mif_dest, 'close'):
                        mif_dest.close()
                except Exception:
                    pass
                del mif_dest
            finally:
                tab_obj.close()
            gc.collect()

            replaced = self._replace_crs_in_mif_bytes(temp_mif_path, crs)
            if not replaced:
                raise RuntimeError('Не удалось заменить CoordSys в MIF')

            provider_manager.mif.convert_to_tab(temp_mif_path, final_tab_path)

        finally:
            self._cleanup_mif_files(temp_mif_path)

    def _tab_to_mif(self, temp_tab_path, final_mif_path):
        tab_obj = provider_manager.open({'src': temp_tab_path})
        try:
            mif_dest = self._get_destination(
                final_mif_path, 'mif', tab_obj.schema)
            mif_dest.export(tab_obj.items())
            try:
                if hasattr(mif_dest, 'close'):
                    mif_dest.close()
            except Exception:
                pass
            del mif_dest
        finally:
            tab_obj.close()
        gc.collect()

    def _tab_to_shp(self, temp_tab_path, final_shp_path):
        tab_obj = provider_manager.open({'src': temp_tab_path})
        try:
            shp_dest = self._get_destination(
                final_shp_path, 'shp', tab_obj.schema)
            shp_dest.export(tab_obj.items())
            try:
                if hasattr(shp_dest, 'close'):
                    shp_dest.close()
            except Exception:
                pass
            del shp_dest
        finally:
            tab_obj.close()
        gc.collect()

    def _get_destination(self, out_path, fmt, schema):
        if schema is None:
            schema = Schema()
        if fmt == 'tab':
            return provider_manager.tab.get_destination(out_path, schema)
        elif fmt == 'shp':
            return provider_manager.shp.get_destination(out_path, schema)
        elif fmt == 'mif':
            return provider_manager.mif.get_destination(out_path, schema)
        else:
            raise ValueError('Неподдерживаемый формат: {}'.format(fmt))
