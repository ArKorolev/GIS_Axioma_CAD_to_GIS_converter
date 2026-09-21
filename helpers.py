"""Вспомогательные функции и константы."""

import os

OUTPUT_FORMATS = {
    'TAB (MapInfo/Аксиома)': 'tab',
    'SHP (Shapefile)': 'shp',
    'MIF/MID (MapInfo Interchange)': 'mif',
}

CAD_FILE_FILTER = 'AutoCAD files (*.dwg *.dxf);;DWG (*.dwg);;DXF (*.dxf)'

# ── Точность snap ──
# step = max_bound / SNAP_RESOLUTION — шаг привязки в native единицах

SNAP_RESOLUTION = 1_000_000_000.0  # 10^9

PRECISION_LIMITS = {
    'm':             {'max_bound': 10_000_000,     'step_str': '0.01 м (1 см)'},
    'meter':         {'max_bound': 10_000_000,     'step_str': '0.01 м (1 см)'},
    'mm':            {'max_bound': 100_000_000,    'step_str': '0.1 мм'},
    'millimeter':    {'max_bound': 100_000_000,    'step_str': '0.1 мм'},
    'cm':            {'max_bound': 100_000_000,    'step_str': '0.1 см'},
    'centimeter':    {'max_bound': 100_000_000,    'step_str': '0.1 см'},
    'km':            {'max_bound': 1_000_000,      'step_str': '0.001 км (1 м)'},
    'kilometer':     {'max_bound': 1_000_000,      'step_str': '0.001 км (1 м)'},
    'ft':            {'max_bound': 10_000_000,    'step_str': '0.01 фут'},
    'foot':          {'max_bound': 10_000_000,    'step_str': '0.01 фут'},
    'yd':            {'max_bound': 10_000_000,      'step_str': '0.01 ярд'},
    'yard':          {'max_bound': 10_000_000,      'step_str': '0.01 ярд'},
    'in':            {'max_bound': 100_000_000,    'step_str': '0.1 дюйм'},
    'inch':          {'max_bound': 100_000_000,    'step_str': '0.1 дюйм'},
    'mi':            {'max_bound': 1_000_000,      'step_str': '0.001 миля'},
    'mile':          {'max_bound': 1_000_000,      'step_str': '0.001 миля'},
    'nmi':           {'max_bound': 1_000_000,      'step_str': '0.001 морск. миля'},
    'nautical_mile': {'max_bound': 1_000_000,      'step_str': '0.001 морск. миля'},
    'nauticalmile':  {'max_bound': 1_000_000,      'step_str': '0.001 морск. миля'},
}

# Коэффициенты перевода единиц в миллиметры (для calc_snap_step)
UNIT_MM_FACTOR = {
    'm': 1000, 'meter': 1000,
    'mm': 1, 'millimeter': 1,
    'cm': 10, 'centimeter': 10,
    'km': 1_000_000, 'kilometer': 1_000_000,
    'ft': 304.8, 'foot': 304.8,
    'yd': 914.4, 'yard': 914.4,
    'in': 25.4, 'inch': 25.4,
    'mi': 1_609_344, 'mile': 1_609_344,
    'nmi': 1_852_000, 'nautical_mile': 1_852_000, 'nauticalmile': 1_852_000,
}

# Русские названия единиц
UNIT_RU = {
    'm': 'м', 'meter': 'м',
    'mm': 'мм', 'millimeter': 'мм',
    'cm': 'см', 'centimeter': 'см',
    'km': 'км', 'kilometer': 'км',
    'ft': 'фут', 'foot': 'фут',
    'yd': 'ярд', 'yard': 'ярд',
    'in': 'дюйм', 'inch': 'дюйм',
    'mi': 'миля', 'mile': 'миля',
    'nmi': 'морск. миля', 'nautical_mile': 'морск. миля', 'nauticalmile': 'морск. миля',
}


def sanitize_filename(name):
    """Заменяет недопустимые символы в имени файла на подчёркивание."""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, '_')
    return name.strip()


def file_basename(filepath):
    """Возвращает имя файла без расширения."""
    return os.path.splitext(os.path.basename(filepath))[0]


def format_number(value):
    """Форматирует число с пробелами как разделителями тысяч и 2 знаками."""
    return '{:,.2f}'.format(value).replace(',', ' ')


def get_precision_limits(unit_str):
    """Возвращает лимиты точности для заданной единицы (по умолчанию — метры)."""
    return PRECISION_LIMITS.get(unit_str, PRECISION_LIMITS['m'])


def calc_snap_step(bound, unit_str):
    """Вычисляет шаг привязки (snap) в мм для заданного охвата."""
    mm_per_unit = UNIT_MM_FACTOR.get(unit_str, 1000)
    return bound / SNAP_RESOLUTION * mm_per_unit


def calc_snap_step_str(bound, unit_str):
    """Вычисляет шаг привязки и возвращает его как строку в native единицах."""
    step_native = bound / SNAP_RESOLUTION
    unit_ru = UNIT_RU.get(unit_str, unit_str)
    if step_native >= 100:
        return '{:.0f} {}'.format(step_native, unit_ru)
    elif step_native >= 1:
        return '{:.2f} {}'.format(step_native, unit_ru)
    elif step_native >= 0.01:
        return '{:.3f} {}'.format(step_native, unit_ru)
    else:
        return '{:.5f} {}'.format(step_native, unit_ru)


def get_unit_str(unit):
    """Извлекает строковое обозначение единицы из enum axipy Unit."""
    if unit is None:
        return 'm'
    try:
        return unit.name.lower()
    except AttributeError:
        pass
    s = str(unit).lower().strip()
    if '.' in s:
        s = s.rsplit('.', 1)[-1]
    return s
