"""Регистрация плагина — стабильная вставка после пункта 'Открыть...'."""

from axipy import Plugin, ActionButton, view_manager
from axipy._internal._menu_file_position import _MenuFilePosition
from PySide2.QtGui import QIcon

from .wizard import ConversionWizard


class CadToGisConverterPlugin(Plugin):
    """Точка входа плагина для ГИС Аксиома 7.1."""

    def __init__(self) -> None:
        self._button = ActionButton(
            'Импорт из САПР (DWG/DXF)...',
            on_click=self._on_click,
            icon=QIcon.fromTheme('document_import'),
        )

        menu = _MenuFilePosition()
        # Вставляем после стандартного пункта 'OpenFile' ('Открыть...')
        # Это гарантирует корректное позиционирование без поиска скрытых ID
        menu.insert_after(self._button, 'OpenFile')

    def _on_click(self) -> None:
        wizard = ConversionWizard(view_manager.global_parent)
        wizard.run()

    def unload(self) -> None:
        self._button.remove()
