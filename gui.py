"""Modern Tk front end for Universal SCS Packer."""
from __future__ import annotations

import os
import random
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace
from app_settings import load_settings as load_app_settings, save_settings as save_app_settings


APP_NAME = "Universal SCS Packer"
WINDOWS_APP_ID = "UniversalSCSPacker.Desktop"
_NATIVE_ICON_HANDLES: list[object] = []
PROGRESS_LINE = re.compile(r":\s*\[[#-]+\]\s+\d+/\d+\s+\(\d+%\)\s*$")
LEVEL_MARKER = re.compile(r"^\x1e(DEBUG|INFO|SUCCESS|WARNING|ERROR)\x1f")
GUI_PROGRESS_DETAIL_PREFIX = "\x1dPROGRESS_DETAIL\x1f"
COLORS = {
    "window": "#100a0c", "sidebar": "#190d11", "surface": "#211217",
    "surface2": "#2b171d", "border": "#4a252e", "text": "#fff5f6",
    "muted": "#bda5aa", "accent": "#ef3340", "accent_hover": "#ff4d59",
    "console": "#0c0809", "success": "#5ee38b", "warning": "#ffc857",
    "error": "#ff6673", "info": "#67d5ff", "debug": "#8fa8ff",
}


# Syntax, Russian description, example.  Keep this list aligned with USAGE in
# packer.py; the GUI intentionally documents every public command-line option.
HELP_OPTIONS = (
    ("Обзор форматов",
     "Входом может быть папка или архив ZIP, 7z, RAR, TAR и другого формата, поддерживаемого встроенным 7-Zip. Выходом служит SCS-контейнер HashFS v1, HashFS v2, Ghostly eZIP либо AEM. Расширение входного архива не используется для распознавания его формата.",
     r"-i .\source.rar -o .\mod.scs -fs=v2"),
    ("HashFS и CityHash",
     "HashFS не обязан хранить открытые имена файлов. Вместо пути в таблицу записывается 64-битный CityHash64. Игра вычисляет тот же хеш запрошенного пути и находит запись. Поэтому скрытый путь невозможно восстановить только из таблицы — экстрактору нужны списки путей, ссылки из файлов или подбор структуры.",
     r"-fs=v1 -h=all"),
    ("Соль хеширования",
     "Соль добавляется к пути перед CityHash64 и меняет все хеши архива. HashFS v2 по умолчанию получает случайную ненулевую соль. HashFS v1 по умолчанию использует 0; при ненулевой соли пакер создаёт совместимую ZIP-заглушку stub_salt0.scs.",
     r"-fs=v2 --salt=random"),
    ("Безопасность скрытия",
     "Модели и материалы игра обычно находит по прямым ссылкам, но прописка def, unit/*.sii, locale и секторы карты могут зависеть от списков каталогов. Обычное -h скрывает их только при подтверждении пути через original_game_paths, reference или текстовую ссылку. Все /unit/**/*.sui разрешено скрывать всегда. -fh полностью обходит защиту.",
     r"-h=all -ref .\base.scs"),
    ("HashFS v2 и GDeflate",
     "В HashFS v2 корректная пара TOBJ+DDS объединяется в одну текстурную запись. DDS переводится в DX10 при необходимости и поверхность сжимается GDeflate. Если одна DDS используется несколькими TOBJ, её данные входят в каждую самостоятельную пару.",
     r"-fs=v2 -cm=max"),
    ("Таблицы HashFS",
     "HashFS v1 имеет одну таблицу записей. HashFS v2 разделяет таблицу хешей путей и метаданные; для каждой можно выбрать положение и сжатие. Положение 1 — 0x1000, 2 — конец, 3 — сразу после заголовка, 4 — случайное место среди данных.",
     r"-fs=v2 -tm=1t2mum"),
    ("Несколько входов и выходов",
     "При одном выходе все входы накладываются по порядку, причём поздний файл заменяет ранний. При нескольких выходах входы делятся на последовательные группы: 7 входов и 3 выхода дают группы 3, 3 и 1. DDS-зависимость переносится к архиву содержащему TOBJ. После каждого выходного файла можно указать -> и одну строку его параметров; подробность вывода остаётся общей.",
     r"-i a.zip b.zip c.zip -o first.scs second.scs"),
    ("-i <источник...>",
     "Одна или несколько папок либо архивов. Поддерживаются ZIP, 7z, RAR, TAR и другие форматы, распознаваемые встроенным 7-Zip. Поздний источник заменяет совпадающий путь из более раннего.",
     r"-i .\base.zip .\patch.7z .\mod"),
    ("-o <архив.scs...>",
     "Один или несколько выходных архивов. Входы делятся на последовательные группы. Например, семь входов и три выхода распределяются как 3, 3 и 1.",
     r"-o .\part1.scs .\part2.scs"),
    ("--file-system, -fs", "Формат результата: hashfsv2/v2, hashfsv1/v1, eZIP/ez или AEM/aem. По умолчанию используется HashFS v2.", "-fs=v2"),
    ("--compression-method, -cm", "Сжатие обычных записей HashFS: max, medium, min или no. Игра штатно читает сжатые записи, а практическая разница в скорости обычно почти отсутствует. Режим no в основном лишь значительно увеличивает размер архива. GDeflate-текстуры HashFS v2 обрабатываются обязательно независимо от этого параметра.", "-cm=max"),
    ("--salt, -salt", "Соль хешей HashFS: 0…65535, шестнадцатеричное число либо random. По умолчанию: random для v2 и 0 для v1.", "--salt=random"),
    ("--table-mode, -tm", "Режим таблиц HashFS. Для v1 — 1…4. Для v2 задаёт позиции t/m и сжатие: например 1t2mum, 2t2mc или random.", "-tm=2t2mc"),
    ("-e <список>, -e=all", "Зашифровать выбранные SII оболочкой 3nK. Значение all выбирает все упаковываемые SII.", "-e=all"),
    ("-h <список>, -h=all", "Скрыть выбранные пути из списков каталогов с учётом проверок видимости прописки, locale и секторов карты.", "-h=all"),
    ("--force-hide, -fh <list.txt>", "Прочитать пути из текстового файла list.txt и принудительно скрыть их, минуя проверки безопасности. Один путь на строку. Небезопасный список может сделать часть мода невидимой игре.", r"-fh .\force-hide.txt"),
    ("-x <список>", "Полностью исключить пути из упаковки. Файл содержит по одному пути на строку.", r"-x .\exclude.txt"),
    ("-ref <источник>", "Папка или архив зависимости для безопасного скрытия путей. Обычно его содержимое в результат не добавляется, но DDS из него автоматически добавляется, если TOBJ мода ссылается на отсутствующую текстуру.", r"-ref .\base.scs"),
    ("-af", "Сохранять пустые файлы. Без параметра нулевые файлы пропускаются.", "-af"),
    ("-ad", "Сохранять пустые каталоги. HashFS v1/v2 записывают их как списки каталогов, eZIP/AEM — как явные ZIP-записи. Для HashFS параметр отключается при одновременном -h, чтобы не раскрывать скрытые пути.", "-ad"),
    ("-a", "Сокращение для одновременного -af и -ad.", "-a"),
    ("--entry-sort, -es", "Порядок eZIP/AEM: enabled/y — каталоги сначала и сортировка; disabled/n — перемешивание при сохранении родителя перед потомками. По умолчанию disabled.", "-es=n"),
    ("--dry-run", "Полностью прочитать, подготовить и сжать HashFS без создания или изменения выходного архива.", "--dry-run"),
    ("--crash-game, -cg",
     "Только HashFS v1. Значение ByManifest (сокращённо BM) намеренно завышает compressed и uncompressed размеры manifest.sii. Игра с таким модом больше не запустится.",
     "-fs=v1 --crash-game=ByManifest"),
    ("Развлечение / Junk", "Для HashFS здесь создаются записи категорий DuplicateHash, MetadataOutOfBounds, InvalidMetadata, PayloadOutOfBounds, PayloadOverlapsTable, InvalidZlibHeader и SuspiciousCompressionRatio. Для eZIP и AEM настройка EOCD=1 добавляет ложный внешний заголовок и намеренно неверные служебные данные конца архива; FE=N добавляет до 10000 ложных правдоподобных записей без payload: примерно треть в начале, остальные случайно после законченных настоящих entries. Для HashFS v2 допускаются 0 или 2 записи DuplicateHash; для HashFS v1 — до 50000.", "--junk=\"EOCD=1; FE=100\""),
    ("--verbose[=min|bar|max]", "Полная форма управления выводом. min показывает этапы и итог, bar добавляет индикаторы прогресса, max выводит каждую записываемую запись. Параметр --verbose без значения означает max.", "--verbose=bar"),
    ("-v0", "Минимальный вывод: этапы и итоговые сообщения.", "-v0"),
    ("-v1", "Этапы и индикатор прогресса. Режим по умолчанию.", "-v1"),
    ("-v2, -v", "Максимальный вывод с отдельной строкой для каждой записываемой записи.", "-v2"),
    ("--help, -?", "Показать полную консольную справку.", "--help"),
)

DETAILED_HELP_RU = {
    "Обзор форматов": """Расширение .scs не обозначает один конкретный формат: игра использует его как общее имя контейнера мода. Пакер определяет входной формат по содержимому, поэтому источником может быть папка, обычный ZIP, SCS, 7z, RAR, TAR, tar.gz и другой архив, который способен открыть встроенный 7-Zip.

HashFS v1 — классический формат SCS. Он содержит заголовок, единую таблицу записей и области данных. Запись хранит CityHash64 пути, смещение, исходный и сжатый размеры и флаги. Обычные данные сжимаются zlib. Списки каталогов являются такими же записями и позволяют игре перечислять дочерние имена.

HashFS v2 разделяет таблицу хешей и метаданные. Метаданные описывают обычный файл, каталог либо специальную текстурную запись. Списки каталогов имеют бинарный формат, а пары TOBJ+DDS могут объединяться и сжиматься GDeflate. V2 лучше подходит для современных ресурсов и обычно создаёт более компактные текстурные архивы.

Ghostly eZIP основан на ZIP: данные остаются обычным Deflate, но в заголовках записей выставлен флаг шифрования без фактического ZIP-шифрования. AEM использует локальные записи AEM! с намеренно обнулённой метаинформацией; имена, размеры и методы сжатия берутся из обфусцированной ZIP-подобной центральной таблицы. Эти форматы не поддерживают соль, скрытие HashFS, таблицы HashFS и GDeflate.

Архив может содержать любые игровые файлы. Для .sii доступна дополнительная оболочка 3nK. Для HashFS v2 TOBJ и DDS обрабатываются особо; остальные файлы сохраняются обычными записями. Пустые файлы по умолчанию пропускаются, а пустые каталоги можно сохранить во всех форматах через -ad. В HashFS одновременный -h отключает сохранение пустых каталогов, чтобы не раскрывать скрытые пути.""",
    "HashFS и CityHash": """HashFS работает как таблица поиска по хешу, а не как ZIP со списком полных имён. Перед упаковкой путь нормализуется: используются прямые слеши, ведущий слеш не участвует в хешировании. Затем вычисляется 64-битный CityHash64. В таблицу попадает это число, а не обязательно исходная строка пути.

Когда игра запрашивает /def/city/example.sii, она нормализует тот же путь, применяет соль архива и CityHash64, после чего ищет совпадающую запись. Совпадение указывает на метаданные, смещение и способ чтения полезной нагрузки. Хеш не является шифрованием содержимого и не позволяет математически получить исходный путь обратно.

Каталоги в HashFS представлены отдельными листингами. Они содержат имена непосредственных файлов и дочерних каталогов. Если листинг сохранён, игра может обходить дерево и узнавать пути. Если запись файла присутствует, но её имя удалено из листинга, файл становится «скрытым»: игра всё ещё откроет его по точному известному пути, однако перечислением каталогов имя уже не обнаружит.

Именно поэтому экстрактору для скрытых записей нужны известные пути, ссылки из SII/PMD/MAT/TOBJ, структура соседних файлов или словарь путей. Одинаковый CityHash при одинаковой соли всегда даёт одинаковый результат; таблица сортируется по хешам, поскольку игра ожидает такой порядок для поиска.""",
    "Соль хеширования": """Соль — 16-битное значение от 0 до 65535, участвующее в вычислении хеша пути. В реализации SCS перед нормализованным путём добавляется десятичное представление соли, после чего вычисляется CityHash64. Поэтому один и тот же файл с разной солью получает разные значения таблицы.

Соль не шифрует данные, не меняет zlib/GDeflate и не защищает содержимое криптографически. Она только создаёт другое пространство хешей и затрудняет применение готовой таблицы путей от другого архива. Для распаковки путь необходимо проверять с солью, записанной в заголовке.

HashFS v2 по умолчанию использует случайную ненулевую соль. HashFS v1 по умолчанию использует 0 ради максимальной совместимости. Ненулевая соль v1 может конфликтовать с объединённым поиском записей нескольких модов; поэтому пакер создаёт отдельную ZIP-заглушку stub_salt0.scs с каталогом /base/def. Заглушка и salted-архив включаются как два файла мода.

Значение random выбирается заново при каждой упаковке. Явное число делает сборку воспроизводимой: одинаковые пути и соль дают одинаковые хеши, хотя сжатые байты и расположение таблиц всё равно могут зависеть от других настроек.""",
    "Безопасность скрытия": """Скрытие означает удаление имени из листинга каталога при сохранении самой хешированной записи. Оно безопасно только тогда, когда игра уже знает точный путь. Модели, материалы, TOBJ и многие DDS обычно достигаются по прямым ссылкам из других ресурсов, поэтому их можно скрывать значительно свободнее.

Прописка def, файлы /unit/**/*.sii, локализация locale и секторы карты чувствительнее. Игра или другой мод может перечислять соответствующий каталог вместо обращения к заранее известному имени. Если убрать единственный источник имени, запись физически останется в архиве, но игра её не запросит. Все /unit/**/*.sui считаются безопасными для скрытия; для unit/*.sii по-прежнему требуется original_game_paths, reference либо явная текстовая ссылка.

Обычный -h применяет проверки. Путь разрешается скрыть, если он присутствует в original_game_paths, имеется в reference-источнике, явно записан в анализируемом текстовом файле либо относится к подтверждённой категории, где родительская прописка всегда перечисляет дочерние пути. Небезопасные элементы остаются видимыми.

-fh принимает именно текстовый list.txt: один архивный путь или каталог на строку. Он полностью обходит перечисленные проверки. Это предназначено для случаев, когда автор точно знает внешний источник пути. Ошибочный force-hide способен сделать отдельные определения, грузы, транспорт, locale или карту невидимыми игре. -x работает иначе: перечисленные записи вообще не упаковываются.""",
    "HashFS v2 и GDeflate": """HashFS v2 имеет специальный тип текстурной записи. Пакер читает бинарный TOBJ, разрешает указанный им DDS и проверяет формат поверхности. Устаревший DDS при необходимости переводится в заголовок DX10; неподдерживаемое расположение пикселей вызывает понятную ошибку вместо создания повреждённого архива.

TOBJ и поверхность DDS объединяются в одну запись. Метаданные хранят размеры, формат, mip-уровни, cubemap-флаги и параметры семплирования TOBJ. Поверхность выравнивается по требованиям DirectStorage и сжимается настоящим GDeflate. При извлечении из одной записи восстанавливаются два логических файла — TOBJ и DDS.

Если несколько TOBJ используют одну DDS, каждая пара должна оставаться самостоятельной, поэтому сжатая поверхность может повторяться. Это немного увеличивает итоговый размер, но обеспечивает корректный поиск каждой пары игрой. В проверенном моде такие повторы занимали около 11 MiB.

GDeflate для текстур v2 обязателен как формат записи, однако уже несжимаемая поверхность может храниться raw внутри того же типа. Параметр -cm=no относится к обычным файлам и листингам, а не отменяет обработку текстур. HashFS v1, eZIP и AEM специальных GDeflate-записей не создают.""",
    "Таблицы HashFS": """HashFS v1 использует единую таблицу фиксированных записей. Положение 1 — стандартный offset 0x1000; положение 3 — сразу после заголовка, то есть фактическое начало служебных данных; положение 2 — конец архива; положение 4 — случайная граница среди полезных нагрузок. Данные и таблица выравниваются, а смещения записываются в заголовок.

HashFS v2 разделяет path table и metadata table. Path table связывает отсортированный CityHash64 с индексом метаданных. Metadata table сообщает тип записи, размеры, смещение, способ сжатия и дополнительные параметры. Обе таблицы можно независимо разместить в начале, на 0x1000, в конце или среди данных, а также независимо сжать zlib.

Стандартный режим v2 размещает обе таблицы в конце и сжимает их; файлы начинаются сразу после заголовка. Это наиболее компактный и предсказуемый вариант. Несжатые таблицы удобнее для анализа, но занимают больше места. Игра нормально читает поддерживаемые сжатые таблицы, и практической разницы в загрузке обычно нет.

Хеши внутри path table должны оставаться отсортированными: эксперимент с перемешиванием показал, что игра такие архивы не читает. Кнопка рандомизации меняет только разрешённые положения и признаки сжатия, но никогда не нарушает сортировку хешей.""",
}

DETAILED_HELP_EN = {
    "Обзор форматов": """The .scs extension is a generic mod-container name, not one physical format. Inputs are detected by content and may be folders, ZIP/SCS, 7z, RAR, TAR, tar.gz, or another format handled by the bundled 7-Zip.

HashFS v1 is the classic SCS format: a header, one fixed entry table, and payload regions. Each row stores a path CityHash64, offset, original/compressed size, and flags. Ordinary payload uses zlib. Directory listings are regular entries that expose immediate child names.

HashFS v2 separates the path-hash table from metadata. Metadata distinguishes files, binary directory listings, and typed texture entries. Valid TOBJ+DDS pairs can be combined and compressed with GDeflate, making v2 especially effective for modern texture-heavy packs.

Ghostly eZIP remains structurally ZIP and uses normal Deflate data, but marks local and central entries as encrypted without applying ZIP encryption. AEM uses AEM! local records with deliberately blank metadata; names, sizes and codecs are authoritative only in its obfuscated ZIP-like central directory. These formats have no HashFS salt, hiding tables, table placement, or GDeflate.

Any game file can be stored. SII optionally supports the 3nK wrapper. TOBJ/DDS receive special handling only in HashFS v2. Empty files are skipped unless requested; explicit eZIP/AEM directory entries are optional and only required to preserve empty folders.""",
    "HashFS и CityHash": """HashFS is a hash lookup rather than a ZIP-style list of full names. Paths are normalized to forward slashes without a leading slash and converted to a 64-bit CityHash64. The table can store only this number, not the original string.

When the game requests /def/city/example.sii, it normalizes the same path, applies the archive salt, calculates CityHash64, and locates matching metadata and payload. A hash is not content encryption and cannot be reversed into its source path.

Directories are separate listing entries containing immediate child names. Removing a file name from its listing hides it while preserving the hashed payload: the game can still open it by an already-known exact path, but directory enumeration cannot discover it.

An extractor therefore recovers hidden names from known path databases, references inside SII/PMD/MAT/TOBJ, companion-file structure, or guesses. Hashes must remain sorted because the game relies on that order for lookup.""",
    "Соль хеширования": """Salt is a 16-bit value from 0 to 65535. Its decimal representation is prepended to the normalized path before CityHash64, so the same path receives different table hashes under different salts.

Salt does not encrypt payload, alter zlib/GDeflate, or provide cryptographic protection. It only creates another hash namespace and prevents an unrelated ready-made path table from matching directly. Extraction must test names using the salt stored in the header.

HashFS v2 defaults to random non-zero salt. HashFS v1 defaults to zero for compatibility. A salted v1 build may conflict with pooled lookup across mods, so the packer creates the separate ZIP stub_salt0.scs containing /base/def. Both files are enabled as parts of the mod.

random chooses a new value for every build. An explicit salt makes path hashes reproducible.""",
    "Безопасность скрытия": """Hiding removes a name from a directory listing while retaining its hashed entry. It is safe only when the game already knows the exact path. Models, materials, TOBJ, and many DDS resources are normally reached through direct references and are therefore easier to hide safely.

Definitions under def, /unit/**/*.sii files, locale resources, and map sectors may instead be discovered by directory enumeration. Removing their only name source leaves bytes in the archive but prevents the game from requesting them. Every /unit/**/*.sui is considered safe to hide; unit/*.sii still requires original_game_paths, a reference source, or an explicit text reference.

Normal -h applies safeguards. A protected path is hidden only when proven by original_game_paths, a reference source, an explicit text-file reference, or a verified family whose parent definition always names its children. Unsafe candidates remain visible.

-fh accepts a text list.txt only, one archive path or directory per line, and bypasses every safeguard. Use it only when an external exact-path source is known. -x is different: it omits listed entries from the archive completely.""",
    "HashFS v2 и GDeflate": """HashFS v2 supports a typed texture record. The packer parses binary TOBJ, resolves its DDS, validates the surface, and converts supported legacy DDS headers to DX10. Unsupported pixel layouts fail clearly rather than producing a corrupt pack.

TOBJ and DDS surface become one record. Metadata stores dimensions, format, mip levels, cubemap state, and TOBJ sampling flags. The DirectStorage layout is aligned and compressed with genuine GDeflate. Extraction reconstructs two logical files from that one table entry.

If several TOBJs share one DDS, each pair remains independent and the compressed surface may be repeated. This can increase archive size, but preserves correct game lookup for every TOBJ path.

-cm=no affects ordinary files and listings, not mandatory v2 texture processing. An incompressible surface may be stored raw inside the same texture-record type. V1, eZIP, and AEM do not create these records.""",
    "Таблицы HashFS": """HashFS v1 has one fixed entry table. Placement 1 is standard offset 0x1000, 3 follows the header, 2 is archive end, and 4 is a random boundary among payloads. Header offsets and alignment describe the selected layout.

HashFS v2 separates a path table from a metadata table. The path table maps sorted CityHash64 values to metadata indexes. Metadata contains type, offset, sizes, compression, and type-specific fields. Each table can independently be placed at the start, 0x1000, end, or among payloads, and each may independently use zlib.

The default places both compressed tables at the end while payload starts immediately after the header. This is compact and predictable. Uncompressed tables are easier to inspect but larger. The game reads supported compressed tables normally, with almost no practical loading difference.

Path hashes must remain sorted; game tests showed that shuffled hashes are unreadable. Randomization changes only supported placement and compression choices.""",
}

HELP_EN = {
    "Обзор форматов": "Inputs may be folders or ZIP, 7z, RAR, TAR and other archives supported by the bundled 7-Zip backend. Outputs may be HashFS v1, HashFS v2, Ghostly eZIP or AEM SCS containers. Input type is detected from content, not its extension.",
    "HashFS и CityHash": "HashFS does not have to store clear file names. The table may contain only a 64-bit CityHash64 of each path. The game hashes a requested path the same way. A hidden name therefore requires a path list, references from other files, or structural discovery to recover.",
    "Соль хеширования": "Salt is prepended before CityHash64 and changes every archive hash. HashFS v2 defaults to a random non-zero salt. HashFS v1 defaults to zero; a salted v1 build also creates the compatibility ZIP stub_salt0.scs.",
    "Безопасность скрытия": "Models and materials are normally reached through direct references, while def, unit/*.sii, locale and map sectors may depend on directory listings. Normal -h hides protected paths only when proven by original_game_paths, a reference source, or an explicit text reference. Every /unit/**/*.sui is always allowed to be hidden. -fh bypasses all protection.",
    "HashFS v2 и GDeflate": "HashFS v2 combines a valid TOBJ+DDS pair into one texture entry. DDS is converted to DX10 when necessary and its surface is compressed with GDeflate. A DDS shared by several TOBJs is stored in every independent pair.",
    "Таблицы HashFS": "HashFS v1 has one entry table. HashFS v2 separates the path-hash table and metadata; placement and compression are selectable independently. Position 1 is 0x1000, 2 is archive end, 3 follows the header, and 4 is a random payload location.",
    "Несколько входов и выходов": "With one output, inputs are overlaid in order and later files replace earlier ones. Multiple outputs split inputs into consecutive groups: seven inputs and three outputs become 3, 3 and 1. A DDS dependency follows the output containing its TOBJ. Each output may be followed by -> and one quoted option string; verbosity remains global.",
    "-i <источник...>": "One or more folders or archives. ZIP, 7z, RAR, TAR and every format recognized by the bundled 7-Zip are supported. A later source replaces the same path from an earlier source.",
    "-o <архив.scs...>": "One or more output archives. Inputs are split into consecutive groups; seven inputs and three outputs are distributed as 3, 3 and 1.",
    "--file-system, -fs": "Output format: hashfsv2/v2, hashfsv1/v1, eZIP/ez or AEM/aem. HashFS v2 is the default.",
    "--compression-method, -cm": "Compression for ordinary HashFS entries: max, medium, min or no. The game reads compressed entries normally and the practical runtime difference is usually negligible; no mainly makes the archive much larger. HashFS v2 GDeflate texture processing remains mandatory.",
    "--salt, -salt": "HashFS salt: 0–65535, hexadecimal, or random. Defaults to random for v2 and zero for v1.",
    "--table-mode, -tm": "HashFS table layout. V1 accepts 1–4. V2 controls table/metadata placement and compression, for example 1t2mum, 2t2mc or random.",
    "-e <список>, -e=all": "Encrypt selected SII files with the 3nK wrapper. all selects every packed SII.",
    "-h <список>, -h=all": "Hide selected paths from directory listings while applying safety checks for definitions, locale and map sectors.",
    "--force-hide, -fh <list.txt>": "Read paths from a text list.txt file, one archive path per line, and force-hide them without safety checks. Unsafe selections may make the mod invisible.",
    "-x <список>": "Completely exclude every path listed in the text file.",
    "-ref <источник>": "Dependency folder or archive used for safe hiding decisions. Reference contents are not packed.",
    "-af": "Keep zero-byte files; otherwise they are skipped.",
    "-ad": "Preserve empty directories. HashFS v1/v2 store them as directory listings, while eZIP/AEM use explicit directory entries. For HashFS this option is disabled when -h is active, so hidden directory paths are not exposed.",
    "-a": "Equivalent to using -af and -ad together.",
    "--entry-sort, -es": "eZIP/AEM ordering: enabled/y sorts directories first; disabled/n randomizes entries while keeping parents before descendants. Disabled by default.",
    "--dry-run": "Read, prepare and compress HashFS data without creating or replacing the output archive.",
    "--crash-game, -cg": "HashFS v1 only. ByManifest (BM) deliberately inflates the declared compressed and uncompressed sizes of manifest.sii. The game will no longer start with this mod.",
    "Развлечение / Junk": "For HashFS these controls generate DuplicateHash, MetadataOutOfBounds, InvalidMetadata, PayloadOutOfBounds, PayloadOverlapsTable, InvalidZlibHeader and SuspiciousCompressionRatio records. For eZIP and AEM, EOCD=1 adds a false outer header and deliberately incorrect end-of-archive metadata; FE=N adds up to 10000 believable false rows without payloads, with about one third near the start and the rest randomly placed after complete genuine entries. HashFS v2 permits 0 or 2 duplicate-hash records; HashFS v1 permits up to 50000.",
    "--verbose[=min|bar|max]": "Full verbosity option. min prints phases and summary, bar adds progress bars, and max prints every written entry. Bare --verbose means max.",
    "-v0": "Minimal output: phases and final messages.",
    "-v1": "Phase messages plus a progress bar. This is the default.",
    "-v2, -v": "Maximum detail with one line for every written entry.",
    "--help, -?": "Print the complete console help.",
}

TEXT_EN = {
    "\u0423\u043a\u0430\u0437\u0430\u0442\u044c EOCD \u043d\u0435\u043f\u0440\u0430\u0432\u0438\u043b\u044c\u043d\u043e  (EOCD=1)": "Write an incorrect EOCD  (EOCD=1)",
    "\u041b\u043e\u0436\u043d\u044b\u0435 ZIP entries, \u0434\u043e 10000  (FE=N)": "False ZIP entries, up to 10000  (FE=N)",
    "Обзор форматов": "Format overview", "HashFS и CityHash": "HashFS and CityHash",
    "Соль хеширования": "Hash salt", "Безопасность скрытия": "Hiding safety",
    "Развлечение / Junk": "Entertainment / Junk",
    "HashFS v2 и GDeflate": "HashFS v2 and GDeflate", "Таблицы HashFS": "HashFS tables",
    "Несколько входов и выходов": "Multiple inputs and outputs",
    "  ▣   Упаковка": "  ▣   Packing",
    "  ⚙   Параметры": "  ⚙   Options",
    "  ☰   Настройки": "  ☰   Settings",
    "  >_   Журнал": "  >_   Activity",
    "  ?   Справка": "  ?   Help",
    "-i <источник...>": "-i <source...>",
    "-o <архив.scs...>": "-o <archive.scs...>",
    "-e <список>, -e=all": "-e <list>, -e=all",
    "-h <список>, -h=all": "-h <list>, -h=all",
    "-x <список>": "-x <list>",
    "-ref <источник>": "-ref <source>",
    "Упаковка": "Packing", "Параметры": "Options", "Настройки": "Settings",
    "Журнал": "Activity",
    "Справка": "Help", "Язык": "Language", "Готово": "Ready", "Упаковать": "Pack",
    "Остановить": "Stop", "Запустить в новой вкладке журнала": "Run in a new activity tab",
    "Настроить каждый архив": "Configure each archive",
    "ОБЩИЕ ПАРАМЕТРЫ ЗАПУСКА": "COMMON RUN OPTIONS",
    "Подготовка одного или нескольких SCS-архивов": "Build one or more SCS archives",
    "Формат, таблицы, пути и дополнительные настройки": "Format, tables, paths and advanced settings",
    "События текущей операции": "Events from the current operation",
    "Полный справочник параметров командной строки": "Complete command-line reference",
    "Параметры приложения, производительности и языка": "Application, performance and language settings",
    "ПРОИЗВОДИТЕЛЬНОСТЬ": "PERFORMANCE",
    "Максимальное количество потоков": "Maximum thread count",
    "ИНТЕРФЕЙС": "INTERFACE", "Сохранить настройки": "Save settings",
    "Настройки сохранены": "Settings saved",
    "ВХОДНЫЕ ПАПКИ И АРХИВЫ": "INPUT FOLDERS AND ARCHIVES",
    "ВЫХОДНЫЕ SCS-АРХИВЫ": "OUTPUT SCS ARCHIVES", "+ Файлы": "+ Files",
    "+ Папка": "+ Folder", "+ Новый архив": "+ New archive", "Удалить": "Remove",
    "Выберите выходные архивы": "Choose output archives",
    "Обзор…": "Browse…", "КОМАНДА ЗАПУСКА": "COMMAND PREVIEW",
    "Копировать": "Copy", "ФОРМАТ АРХИВА": "ARCHIVE FORMAT",
    "Файловая система  (--file-system)": "File system  (--file-system)",
    "Метод сжатия": "Compression method", "Соль хеширования": "Hash salt",
    "Положение таблицы": "Table placement", "Положение таблицы путей": "Path-table placement",
    "Таблица путей": "Path table", "Метаданные": "Metadata",
    "Положение": "Placement", "Сжата": "Compressed", "Сжаты": "Compressed",
    "Таблица путей сжата": "Path table compressed", "Положение метаданных": "Metadata placement",
    "Метаданные сжаты": "Metadata compressed", "🎲 Рандомизировать таблицы": "🎲 Randomize tables",
    "Подробность вывода": "Output detail", "Обычный — offset 0x1000": "Normal — offset 0x1000",
    "В самом начале": "At the beginning", "В конце": "At the end",
    "Случайно в середине": "Random payload position", "Минимальный": "Minimal",
    "С индикатором прогресса": "With progress bar", "Подробный — каждый файл": "Detailed — every file",
    "Не использовать": "Disabled", "Все файлы": "All files", "Список путей": "Path list",
    "⚠ Без сжатия архив займёт значительно больше места.": "⚠ An uncompressed archive uses significantly more space.",
    "⚠ Игра нормально читает сжатые файлы; практической разницы в работе почти нет. Режим no только заметно увеличивает архив.": "⚠ The game reads compressed files normally and there is almost no practical runtime difference. no only makes the archive significantly larger.",
    "СПИСКИ ПУТЕЙ И ЗАВИСИМОСТИ": "PATH LISTS AND DEPENDENCIES",
    "Шифрование SII  (-e)": "SII encryption  (-e)", "Скрытие путей  (-h)": "Path hiding  (-h)",
    "Файл списка принудительного скрытия (.txt)  (-fh)": "Force-hide list file (.txt)  (-fh)",
    "Исключить по списку  (-x)": "Exclude list  (-x)",
    "ДОПОЛНИТЕЛЬНЫЕ ПАРАМЕТРЫ": "ADVANCED OPTIONS",
    "РАЗВЛЕЧЕНИЕ": "ENTERTAINMENT",
    "МУСОРНЫЕ ЗАПИСИ (АНАЛОГ GMC PACKER)": "JUNK RECORDS (GMC PACKER STYLE)",
    "СЛОМАТЬ ИГРУ": "BREAK THE GAME",
    "Дубликаты хэша": "Duplicate hashes",
    "Метаданные за границами таблицы": "Metadata out of bounds",
    "Некорректные метаданные": "Invalid metadata",
    "Данные за границами архива": "Payload out of bounds",
    "Данные перекрывают таблицу": "Payload overlaps table",
    "Некорректный заголовок zlib": "Invalid zlib header",
    "Неизвестный тип metadata-блока (только v2)": "Unknown metadata chunk type (v2 only)",
    "Подозрительное сжатие": "Suspicious compression ratio",
    "Случайные корректные записи": "Random valid entries",
    "Ложный metadata-дескриптор перед настоящим, только HashFS v2  (DMD=1)":
        "Decoy metadata descriptor before the real one, HashFS v2 only  (DMD=1)",
    "Сделать игру незапускаемой  (-cg=BM)":
        "Prevent the game from starting  (-cg=BM)",
    "Размеры compressed и uncompressed для manifest.sii будут намеренно завышены. Игра с таким модом больше не запустится.":
        "The compressed and uncompressed sizes of manifest.sii will be deliberately inflated. The game will no longer start with this mod.",
    "🎲 Рандомизировать значения": "🎲 Randomize values",
    "HashFS v2: дубликаты 0 или 2. HashFS v1: до 50000. Подозрительное сжатие: до 50000; случайные корректные: до 1000; остальные категории: до 30.":
        "HashFS v2: duplicate count is 0 or 2. HashFS v1: up to 50000. Suspicious compression: up to 50000; random valid entries: up to 1000; all other categories: up to 30.",
    "Сохранять пустые файлы  (-af)": "Keep empty files  (-af)",
    "Добавлять записи каталогов  (-ad)": "Preserve empty directories  (-ad)",
    "Сохранять пустые каталоги  (-ad)": "Preserve empty directories  (-ad)",
    "Сортировать записи  (-es=y)": "Sort entries  (-es=y)",
    "Перемешивать payload  (-spl=y)": "Shuffle payloads  (-spl=y)",
    "Проверка без записи  (--dry-run)": "Dry run  (--dry-run)",
    "Вывод текущей операции": "Current operation output", "+ Новая вкладка": "+ New tab",
    "Очистить": "Clear", "Полная справка по параметрам": "Complete option reference",
    "Описание": "Description", "Пример": "Example", "Ничего не найдено": "Nothing found",
    "Копировать текст": "Copy text", "Выделить всё": "Select all",
    "Введите имя или путь нового SCS-архива:": "Enter the name or path of the new SCS archive:",
    "Новый выходной архив": "New output archive", "Выходной архив": "Output archive",
    "Имя файла": "File name", "Папка": "Folder", "Выберите папку назначения": "Choose destination folder",
    "Введите имя файла.": "Enter a file name.", "Создать": "Create", "Отмена": "Cancel",
    "Выберите выходной архив": "Choose output archive", "SCS-архив": "SCS archive",
    "Выберите входные архивы": "Choose input archives", "Выберите входную папку": "Choose input folder",
    "Выберите список путей": "Choose path list", "Выберите reference-архив": "Choose reference archive",
    "Архивы": "Archives", "Все файлы": "All files", "Текстовые файлы": "Text files",
    "Добавьте хотя бы один входной архив или папку.": "Add at least one input archive or folder.",
    "Выходных архивов не может быть больше, чем входов.": "There cannot be more output archives than inputs.",
    "Упаковка уже выполняется.": "Packing is already running.",
    "Закрыть вкладку": "Close tab",
    "Нельзя закрыть вкладку, пока в ней выполняется упаковка.":
        "The tab cannot be closed while packing is running in it.",
    "Дождитесь завершения текущей упаковки.": "Wait for the current packing operation to finish.",
    "Команда скопирована": "Command copied", "Текст скопирован": "Text copied",
    "Выполняется…": "Running…", "Остановка…": "Stopping…",
    "Завершено успешно": "Completed successfully", "Упаковка выполняется. Остановить её и выйти?": "Packing is running. Stop it and exit?",
}


def _hide_standalone_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        process_ids = (ctypes.c_uint * 2)()
        if ctypes.windll.kernel32.GetConsoleProcessList(process_ids, 2) <= 1:
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except (AttributeError, OSError):
        pass


def _set_windows_app_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except (AttributeError, OSError):
        pass


def _apply_windows_window_icon(root: tk.Tk, icon_path: Path) -> None:
    """Use the embedded/native ICO for the title bar and taskbar."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        source = str(sys.executable if getattr(sys, "frozen", False) else icon_path)
        large = wintypes.HICON()
        small = wintypes.HICON()
        shell32.ExtractIconExW.argtypes = (
            wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
            ctypes.POINTER(wintypes.HICON), wintypes.UINT)
        shell32.ExtractIconExW.restype = wintypes.UINT
        if not shell32.ExtractIconExW(source, 0, ctypes.byref(large),
                                      ctypes.byref(small), 1):
            return
        root.update_idletasks()
        client = wintypes.HWND(root.winfo_id())
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        wrapper = user32.GetParent(client)
        user32.SendMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.SendMessageW.restype = ctypes.c_ssize_t
        for window in (client, wrapper):
            if window:
                if small:
                    user32.SendMessageW(window, 0x0080, 0, small.value)
                if large:
                    user32.SendMessageW(window, 0x0080, 1, large.value)
        _NATIVE_ICON_HANDLES.extend((large, small))
    except (AttributeError, OSError, tk.TclError):
        pass


def _display_path(path: str) -> str:
    r"""Use .\ for paths located beside the application/current directory."""
    try:
        absolute = Path(path).resolve()
        relative = absolute.relative_to(Path.cwd().resolve())
        return ".\\" + str(relative)
    except (OSError, ValueError):
        return str(path)


def _translate_output_ru(line: str) -> str:
    """Translate packer runtime output while leaving paths and numbers intact."""
    newline = "\n" if line.endswith("\n") else ""
    text = line.rstrip("\r\n")
    patterns: tuple[tuple[str, str], ...] = (
        (r"^Inputs: (\d+)$", r"Входных источников: \1"),
        (r"^Outputs: (\d+)$", r"Выходных архивов: \1"),
        (r"^Output (\d+)/(\d+): (.*)$", r"Выходной архив \1/\2: \3"),
        (r"^  Inputs: (.*)$", r"  Источники: \1"),
        (r"^(.+?) archive (.+) created successfully\.$",
         r"\1 архив \2 успешно создан."),
        (r"^(.+?) archive (.+) planned successfully\.$",
         r"\1 архив \2 успешно запланирован."),
        (r"^Final archive results:$", r"Итоговые сведения об архивах:"),
        (r"^Table moved to offset (0x[0-9A-F]+) because it overlapped the metadata\.$",
         r"Таблица перемещена на оффсет \1 из-за перекрытия метаданными."),
        (r"^Metadata moved to offset (0x[0-9A-F]+) because it overlapped the table\.$",
         r"Метаданные перемещены на оффсет \1 из-за перекрытия таблицей."),
        (r"^Reading input (\d+) (ZIP|TAR): (.*)$",
         r"Чтение входного источника \1 (\2): \3"),
        (r"^Reading input (\d+) archive: (.*)$",
         r"Чтение входного источника \1 (архив): \2"),
        (r"^Reading reference (ZIP|TAR): (.*)$",
         r"Чтение источника зависимостей (\1): \2"),
        (r"^Reading reference archive: (.*)$",
         r"Чтение источника зависимостей (архив): \1"),
        (r"^Parallel preparation: (\d+) worker\(s\)\.$",
         r"Параллельная подготовка: \1 потоков."),
        (r"^Parallel HashFS v2 preparation: (\d+) worker\(s\)\.$",
         r"Параллельная подготовка HashFS v2: \1 потоков."),
        (r"^Parallel ZIP compression: (\d+) worker\(s\)\.$",
         r"Параллельное сжатие ZIP: \1 потоков."),
        (r"^Validating and converting (\d+) DDS file\(s\)\.\.\.$",
         r"Проверка и преобразование \1 файлов DDS…"),
        (r"^DDS conversion: (\d+) legacy file\(s\) converted to DX10; (\d+) already DX10\.$",
         r"Преобразование DDS завершено: \1 старых файлов переведено в DX10; \2 уже были DX10."),
        (r"^Analyzing (\d+) TOBJ/DDS pair\(s\) for GDeflate\.\.\.$",
         r"Анализ \1 пар TOBJ/DDS для GDeflate…"),
        (r"^GDeflate textures: (\d+) combined; (\d+) compressed; (\d+) stored raw; (\d+) standalone TOBJ; (\d+) pair\(s\) stored separately\.$",
         r"Подготовка GDeflate завершена: \1 пар объединено; \2 сжато; \3 сохранено без сжатия; \4 отдельных TOBJ; \5 пар сохранено раздельно."),
        (r"^ZIP entry preparation completed: (\d+) files, (\d+) directories\.$",
         r"Подготовка записей ZIP завершена: \1 файлов, \2 каталогов."),
        (r"^Source scan complete: (\d+) files, (\d+) folders\.$",
         r"Сканирование источников завершено: \1 файлов, \2 каталогов."),
        (r"^Added (\d+) cross-input DDS dependency file\(s\) for TOBJ entries in output (\d+)\.$",
         r"Добавлено \1 зависимых файлов DDS из других источников для TOBJ в выходном архиве \2."),
        (r"^Adding (\d+) unreachable junk entries\.\.\.$",
         r"Добавление \1 недоступных мусорных записей…"),
        (r"^Added (\d+) junk entries\.$",
         r"Добавлено мусорных записей: \1."),
        (r"^Adding (\d+) junk record\(s\)\.\.\.$",
         r"Добавление мусорных записей: \1…"),
        (r"^Adding (\d+) false ZIP entries\.\.\.$",
         r"Добавление ложных ZIP-записей: \1…"),
        (r"^(?:SUCCESS:\s*)?False ZIP entries added: (\d+)$",
         r"Добавлено ложных ZIP-записей: \1"),
        (r"^(?:SUCCESS:\s*)?Junk records added: (.*)$",
         r"Добавлены мусорные записи: \1"),
        (r"^(?:SUCCESS:\s*)?Decoy metadata descriptors inserted before recognized descriptors\.$",
         r"Ложные дескрипторы метаданных вставлены перед настоящими."),
        (r"^(?:WARNING:\s*)?manifest\.sii declares compressed and uncompressed sizes larger than its payload\. The game will no longer start with this mod\.$",
         r"Игра с таким модом больше не запустится: для manifest.sii заявлены завышенные compressed и uncompressed размеры."),
        (r"^(?:WARNING:\s*)?--crash-game=ByManifest was selected but manifest\.sii is not packed\.$",
         r"Выбран --crash-game=ByManifest, но manifest.sii не упакован."),
        (r"^HashFS record preparation completed: (\d+) files, (\d+) directory listings; payload (.*)\.$",
         r"Подготовка записей HashFS завершена: \1 файлов, \2 списков каталогов; данные \3."),
        (r"^Writing (\d+) archive entries \((\d+) files, (\d+) directories\)\.\.\.$",
         r"Запись \1 элементов архива (\2 файлов, \3 каталогов)…"),
        (r"^Writing (\d+) archive entries \((\d+) files, (\d+) directory listings; payload (.*)\)\.\.\.$",
         r"Запись \1 элементов архива (\2 файлов, \3 списков каталогов; данные \4)…"),
        (r"^Planning (\d+) archive entries \((\d+) files, (\d+) directory listings; payload (.*)\)\.\.\.$",
         r"Расчёт \1 элементов архива (\2 файлов, \3 списков каталогов; данные \4)…"),
        (r"^  Entries: (\d+) \((\d+) files, (\d+) directories\)$",
         r"  Записей: \1 (\2 файлов, \3 каталогов)"),
        (r"^  Entries: (\d+) \((\d+) files, (\d+) directory listings\)$",
         r"  Записей: \1 (\2 файлов, \3 списков каталогов)"),
        (r"^WRITE(\s+\d+/\d+\s+/.*) \[directory; (.*)\]$",
         r"ЗАПИСЬ\1 [каталог; \2]"),
        (r"^WRITE(\s+\d+/\d+\s+/.*) \[file; (.*)\]$",
         r"ЗАПИСЬ\1 [файл; \2]"),
        (r"^A non-zero salt can make pooled lookup incompatible with other mod archives\.$",
         r"Ненулевая соль может нарушить совместный поиск ресурсов с другими архивами модов."),
        (r"^A compatibility ZIP containing only /base/def/ will be created\. It can be enabled in any Mod Manager position\.$",
         r"Будет создана совместимая ZIP-заглушка только с /base/def/. Её можно расположить в любом месте Менеджера модов."),
        (r"^--force-hide bypasses definition and map visibility checks\. Forced files may be invisible to the game\.$",
         r"--force-hide обходит проверки видимости прописки и карты. Принудительно скрытые файлы могут быть не видны игре."),
        (r"^-ad is disabled for HashFS while -h is active, because explicit empty directory entries would expose hidden directory paths\.$",
         r"Для HashFS параметр -ad отключён, пока используется -h: явные записи пустых каталогов раскрыли бы скрытые пути."),
        (r"^--compression-method no stores ordinary entries uncompressed\. Mandatory HashFS v2 GDeflate texture entries remain compressed; disabling other compression wastes disk space\.$",
         r"--compression-method no сохраняет обычные записи без сжатия. Обязательные текстуры GDeflate в HashFS v2 остаются сжатыми; отключение остального сжатия напрасно расходует место."),
        (r"^Force-hidden (\d+) files using (\d+) selector\(s\); game visibility was not verified\.$",
         r"Принудительно скрыто \1 файлов по \2 селекторам; видимость в игре не проверялась."),
        (r"^Redundant root/original/reference directory listings omitted: (\d+)\.$",
         r"Пропущено избыточных списков корневых/original/reference-каталогов: \1."),
        (r"^Hide safety: (\d+) protected files kept visible; (\d+) allowed by mandatory categories; (\d+) allowed by -ref; (\d+) allowed by original-game paths; (\d+) allowed by explicit text-file paths\.$",
         r"Безопасность скрытия: \1 защищённых файлов оставлено видимыми; \2 разрешено обязательными категориями; \3 разрешено через -ref; \4 разрешено путями оригинальной игры; \5 разрешено явными путями из текстовых файлов."),
        (r"^Created (\d+) output archives\.$", r"Создано выходных архивов: \1."),
        (r"^Planned (\d+) output archives\.$", r"Запланировано выходных архивов: \1."),
        (r"^Error: (.*)$", r"Ошибка: \1"),
        (r"^Not enough memory to complete packing\. Reduce the maximum thread count, split the input into several output archives, or close other memory-intensive applications\.$",
         r"Недостаточно памяти для завершения упаковки. Уменьшите максимальное количество потоков, разделите входные данные на несколько выходных архивов или закройте другие ресурсоёмкие приложения."),
    )
    patterns += (
        (r"^Added (\d+) DDS dependency file\(s\) from -ref for TOBJ entries in output (\d+)\.$",
         r"Добавлено \1 DDS-зависимостей из -ref для TOBJ в выходной архив \2."),
        (r"^(?:WARNING:\s*)?(\d+) TOBJ file\(s\) reference DDS textures absent from the input\. With a non-zero HashFS v2 salt, these textures may fail in-game\. It is recommended to extract the game archives and provide their folder through -ref\.$",
         r"\1 TOBJ ссылаются на DDS, отсутствующие во входных файлах. При ненулевой соли HashFS v2 текстуры могут не загрузиться в игре. Рекомендуется распаковать архивы игры и указать их папку через -ref."),
        (r"^(?:WARNING:\s*)?(\d+) TOBJ file\(s\) reference DDS textures absent from both the input and -ref\. With a non-zero HashFS v2 salt, these textures may fail in-game\.$",
         r"\1 TOBJ ссылаются на DDS, отсутствующие и во входных файлах, и в -ref. При ненулевой соли HashFS v2 текстуры могут не загрузиться в игре."),
        (r"^(?:WARNING:\s*)?External texture dependency /(.*) required by /(.*)$",
         r"Внешняя зависимость текстуры /\1 требуется для /\2"),
    )
    junk_names = {
        "DuplicateHash": "Дубликаты хэша",
        "MetadataOutOfBounds": "Метаданные за границами таблицы",
        "InvalidMetadata": "Некорректные метаданные",
        "PayloadOutOfBounds": "Данные за границами архива",
        "PayloadOverlapsTable": "Данные перекрывают таблицу",
        "UnknownChunk": "Неизвестный тип блока",
        "InvalidZlibHeader": "Некорректный заголовок zlib",
        "SuspiciousCompressionRatio": "Подозрительное отношение сжатия",
        "RandomValid": "Случайные корректные записи",
    }
    for pattern, replacement in patterns:
        if re.match(pattern, text, flags=re.IGNORECASE):
            translated = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            for source, target in junk_names.items():
                translated = translated.replace(source, target)
            return translated + newline

    progress_labels = {
        "Preparing HashFS entries": "Подготовка записей HashFS",
        "Preparing HashFS v2 entries": "Подготовка записей HashFS v2",
        "Converting DDS to DX10": "Преобразование DDS в DX10",
        "Encoding GDeflate textures": "Кодирование текстур GDeflate",
        "Compressing ZIP entries": "Сжатие записей ZIP",
        "Writing HashFS v1": "Запись HashFS v1",
        "Writing HashFS v2": "Запись HashFS v2",
        "Writing Ghostly eZIP": "Запись Ghostly eZIP",
        "Writing AEM": "Запись AEM",
    }
    if text.startswith("DETAIL "):
        detail = text[len("DETAIL "):]
        parts = detail.split(" | ", 2)
        if len(parts) == 3:
            label, subject, progress = parts
            shown_path = subject if subject.startswith("/") else "/" + subject
            templates = {
                "Preparing HashFS entries": "Подготовка записи {path}: {progress}",
                "Preparing HashFS v2 entries": "Подготовка записи {path}: {progress}",
                "Converting DDS to DX10": "Преобразование {path} в DX10: {progress}",
                "Encoding GDeflate textures": (
                    "Кодирование текстуры {path} в GDeflate: {progress}"),
                "Compressing ZIP entries": "Сжатие записи {path}: {progress}",
            }
            template = templates.get(label)
            if template is not None:
                return template.format(path=shown_path, progress=progress) + newline
        return detail + newline
    for source, translated in progress_labels.items():
        if text.startswith(source + ":"):
            return translated + text[len(source):] + newline

    phrases = (
        ("WARNING: ", ""), ("SUCCESS: ", ""),
        ("Options:", "Параметры:"), ("Selectors:", "Списки путей:"),
        ("file system=", "файловая система="), ("table mode=", "режим таблицы="),
        ("salt=", "соль="), ("compression=", "сжатие="),
        ("gdeflate=mandatory", "gdeflate=обязательно"),
        ("empty files=", "пустые файлы="),
        ("empty directories=", "пустые каталоги="),
        ("directory entries=", "записи каталогов="),
        ("entry sort=", "сортировка записей="), ("dry run=", "пробный запуск="),
        ("incorrect EOCD=", "неверный EOCD="),
        ("false entries=", "ложные ZIP-записи="),
        ("junk entries=", "мусорные записи="),
        ("junk=", "мусор="),
        ("entertainment=", "развлечение="),
        ("decoy metadata descriptors=", "ложные дескрипторы метаданных="),
        ("crash game=", "поломка игры="),
        ("force-hide=", "принудительное скрытие="),
        ("hide=", "скрытие="), ("encrypt=", "шифрование="),
        ("Loading original-game path list...", "Загрузка списка путей оригинальной игры…"),
        ("Scanning ZIP input files...", "Сканирование входных файлов ZIP…"),
        ("Checking hide-safety rules and dependencies...",
         "Проверка безопасности скрытия и зависимостей…"),
        ("Scanning input files and building directory listings...",
         "Сканирование входных файлов и построение списков каталогов…"),
        ("Preparing HashFS records without compression...",
         "Подготовка записей HashFS без сжатия…"),
        ("Preparing HashFS records and compressing eligible files...",
         "Подготовка записей HashFS и сжатие подходящих файлов…"),
        ("Definition categories always hidden when selected by -h:",
         "Категории прописки, всегда скрываемые при выборе через -h:"),
        ("Definition categories allowed by explicit text-file paths:",
         "Категории прописки, разрешённые явными путями из текстовых файлов:"),
        ("Protected categories always hidden when selected by -h:",
         "Защищённые категории, всегда скрываемые при выборе через -h:"),
        ("Protected categories allowed by explicit text-file paths:",
         "Защищённые категории, разрешённые явными путями из текстовых файлов:"),
        ("(none selected)", "(ничего не выбрано)"), ("(none)", "(нет)"),
        ("Archive size:", "Размер архива:"), ("table offset:", "смещение таблицы:"),
        ("metadata offset:", "смещение метаданных:"),
        ("table mode:", "режим таблицы:"), ("salt:", "соль:"),
        ("Compatibility ZIP stub created:", "ZIP-заглушка совместимости создана:"),
        ("archive created:", "архив создан:"),
        ("Dry run completed; destination was not changed:",
         "Пробная упаковка завершена; выходной файл не изменён:"),
        ("DRY RUN: archive was not written; estimated size",
         "ПРОБНЫЙ ЗАПУСК: архив не записан; расчётный размер"),
        ("DRY RUN: compatibility ZIP stub would be created:",
         "ПРОБНЫЙ ЗАПУСК: была бы создана ZIP-заглушка совместимости:"),
        ("GDEFLATE FALLBACK", "РЕЗЕРВНЫЙ РЕЖИМ GDEFLATE"),
        ("multiple archive formats", "несколько форматов архивов"),
    )
    for source, translated in phrases:
        text = text.replace(source, translated)
    if text.startswith("Параметры:"):
        option_words = {
            "included": "включены", "skipped": "пропускаются",
            "omitted": "не добавляются", "enabled": "включена",
            "disabled": "выключена", "yes": "да", "no": "нет",
        }
        for source, translated in option_words.items():
            text = re.sub(rf"\b{source}\b", translated, text)
    elif text.startswith("Списки путей:"):
        text = re.sub(r"\ball\b", "все", text)
    text = re.sub(r"^Processed (\d+) output archives\.$",
                  r"Обработано выходных архивов: \1.", text)
    return text + newline


def _format_detail_line(line: str, language: str) -> str:
    """Render structured verbose progress without exposing its DETAIL marker."""
    if not line.lstrip().startswith("DETAIL "):
        return _translate_output_ru(line) if language == "ru" else line
    if language == "ru":
        return _translate_output_ru(line)
    newline = "\n" if line.endswith("\n") else ""
    detail = line.strip()[len("DETAIL "):]
    parts = detail.split(" | ", 2)
    if len(parts) != 3:
        return detail + newline
    label, subject, progress = parts
    shown_path = subject if subject.startswith("/") else "/" + subject
    templates = {
        "Preparing HashFS entries": "Preparing record {path}: {progress}",
        "Preparing HashFS v2 entries": "Preparing record {path}: {progress}",
        "Converting DDS to DX10": "Converting {path} to DX10: {progress}",
        "Encoding GDeflate textures": (
            "Encoding texture {path} to GDeflate: {progress}"),
        "Compressing ZIP entries": "Compressing record {path}: {progress}",
    }
    template = templates.get(label, "{path}: {progress}")
    return template.format(path=shown_path, progress=progress) + newline


class PackerGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1180x780")
        self.root.minsize(940, 650)
        self.root.configure(bg=COLORS["window"])
        self._app_settings = load_app_settings()
        self.language_code = ("en" if self._app_settings.get("language") == "en"
                              else "ru")
        self.language_choice = tk.StringVar(
            value="English" if self.language_code == "en" else "Русский")
        self.max_threads = tk.StringVar(
            value=str(self._app_settings.get("max_threads", 1)))
        self.operations: dict[int, dict[str, object]] = {}
        self.operation_counter = 0
        self.log_models: dict[tk.Text, list[dict[str, object]]] = {}
        self.log_record_counter = 0
        self.output_queue: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self.inputs: list[str] = []
        self.outputs: list[str] = []

        self.file_system = tk.StringVar(value="hashfsv2")
        self.compression = tk.StringVar(value="max")
        self.salt = tk.StringVar(value="random")
        self.v1_table_position = tk.StringVar(value="1")
        self.v2_table_position = tk.StringVar(value="2")
        self.v2_metadata_position = tk.StringVar(value="2")
        self.v2_table_compressed = tk.BooleanVar(value=True)
        self.v2_metadata_compressed = tk.BooleanVar(value=True)
        self.verbosity = tk.StringVar(value="progress")
        self.encrypt_mode = tk.StringVar(value="none")
        self.encrypt_list = tk.StringVar()
        self.hide_mode = tk.StringVar(value="none")
        self.hide_list = tk.StringVar()
        self.force_hide = tk.StringVar()
        self.exclude = tk.StringVar()
        self.reference = tk.StringVar()
        self.empty_files = tk.BooleanVar()
        self.directories = tk.BooleanVar()
        self.entry_sort = tk.BooleanVar(value=False)
        self.sort_payload = tk.BooleanVar(value=True)
        self.dry_run = tk.BooleanVar()
        self.junk_duplicate_hash = tk.StringVar(value="0")
        self.junk_metadata_out_of_bounds = tk.StringVar(value="0")
        self.junk_invalid_metadata = tk.StringVar(value="0")
        self.junk_payload_out_of_bounds = tk.StringVar(value="0")
        self.junk_payload_overlaps_table = tk.StringVar(value="0")
        self.junk_invalid_zlib_header = tk.StringVar(value="0")
        self.junk_unknown_chunk = tk.StringVar(value="0")
        self.junk_suspicious_compression = tk.StringVar(value="0")
        self.junk_random_valid = tk.StringVar(value="0")
        self.junk_decoy_metadata_descriptor = tk.BooleanVar(value=False)
        self.junk_incorrect_eocd = tk.BooleanVar(value=False)
        self.junk_false_entries = tk.StringVar(value="0")
        self.crash_game_by_manifest = tk.BooleanVar(value=False)
        self.configure_each_archive = tk.BooleanVar(value=False)
        self.output_settings: dict[str, SimpleNamespace] = {}
        self.output_fold_open: dict[str, bool] = {}
        self._last_file_system = self.file_system.get()
        self.status = tk.StringVar(
            value="Ready" if self.language_code == "en" else "Готово")
        for variable in (
            self.file_system, self.compression, self.salt, self.v1_table_position,
            self.v2_table_position, self.v2_metadata_position,
            self.v2_table_compressed, self.v2_metadata_compressed,
            self.verbosity, self.encrypt_mode, self.encrypt_list, self.hide_mode,
            self.hide_list, self.force_hide, self.exclude, self.reference,
            self.empty_files, self.directories, self.entry_sort, self.sort_payload, self.dry_run,
            self.junk_duplicate_hash, self.junk_metadata_out_of_bounds,
            self.junk_invalid_metadata, self.junk_payload_out_of_bounds,
            self.junk_payload_overlaps_table, self.junk_invalid_zlib_header,
            self.junk_unknown_chunk,
            self.junk_suspicious_compression, self.junk_random_valid,
            self.junk_decoy_metadata_descriptor,
            self.junk_incorrect_eocd,
            self.junk_false_entries,
            self.crash_game_by_manifest,
        ):
            variable.trace_add("write", lambda *_args: self.refresh_command())
        self.configure_each_archive.trace_add(
            "write", self._configure_each_changed)
        self.file_system.trace_add("write", self._file_system_changed)
        self.compression.trace_add("write", self._update_compression_warning)
        self.hide_mode.trace_add("write", self._update_directory_availability)

        self._configure_styles()
        self._build()
        self._install_copy_support()
        if self.language_code == "en":
            # A saved language is known before widgets are created.  Several
            # stable shell controls are intentionally authored in Russian and
            # normally translated by change_language(); perform the same full
            # pass on a cold English startup as well.
            self._translate_subtree(self.root, TEXT_EN)
            self._refresh_activity_tabs()
            self._rerender_all_logs()
            self._filter_help()
            self.show_page(self.current_page)
        self.refresh_command()
        self.root.after(50, self._drain_output)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_styles(self) -> None:
        self.root.option_add("*TCombobox*Listbox.background", COLORS["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*Entry.background", COLORS["surface2"])
        self.root.option_add("*Entry.foreground", COLORS["text"])
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=COLORS["window"], foreground=COLORS["text"],
                        fieldbackground=COLORS["surface2"], bordercolor=COLORS["border"],
                        lightcolor=COLORS["border"], darkcolor=COLORS["border"],
                        font=("Segoe UI", 10))
        style.configure("TFrame", background=COLORS["window"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("Card.TLabelframe", background=COLORS["surface"],
                        foreground=COLORS["muted"], bordercolor=COLORS["border"],
                        relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=COLORS["surface"],
                        foreground=COLORS["muted"], font=("Segoe UI Semibold", 9))
        style.configure("TLabel", background=COLORS["window"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("Warning.Card.TLabel", background=COLORS["surface"],
                        foreground=COLORS["warning"], font=("Segoe UI Semibold", 9))
        style.configure("Muted.TLabel", background=COLORS["window"], foreground=COLORS["muted"])
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22),
                        foreground=COLORS["text"], background=COLORS["window"])
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 10),
                        foreground=COLORS["accent"], background=COLORS["surface"])
        style.configure("TEntry", fieldbackground=COLORS["surface2"],
                        foreground=COLORS["text"], insertcolor=COLORS["text"],
                        bordercolor=COLORS["border"], padding=7)
        style.map("TEntry", fieldbackground=[("readonly", COLORS["surface2"]),
                                              ("disabled", COLORS["surface2"])],
                  foreground=[("readonly", COLORS["text"]),
                              ("disabled", COLORS["muted"])])
        style.configure("TCombobox", fieldbackground=COLORS["surface2"],
                        background=COLORS["surface2"], foreground=COLORS["text"],
                        arrowcolor=COLORS["muted"], bordercolor=COLORS["border"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["surface2"])],
                  background=[("readonly", COLORS["surface2"])],
                  foreground=[("readonly", COLORS["text"])])
        style.configure("TButton", padding=(11, 7), background=COLORS["surface2"],
                        foreground=COLORS["text"], borderwidth=1)
        style.map("TButton", background=[("active", COLORS["border"])])
        style.configure("Accent.TButton", padding=(18, 9),
                        font=("Segoe UI Semibold", 10),
                        background=COLORS["accent"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", COLORS["accent_hover"]),
                                                 ("disabled", COLORS["border"])])
        style.configure("TCheckbutton", background=COLORS["surface"],
                        foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["surface"])])
        style.configure("TRadiobutton", background=COLORS["surface"],
                        foreground=COLORS["text"])
        style.map("TRadiobutton", background=[("active", COLORS["surface"])])
        style.configure("TNotebook", background=COLORS["window"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(15, 7), background=COLORS["surface"],
                        foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.map("TNotebook.Tab", background=[("selected", COLORS["surface2"])],
                  foreground=[("selected", COLORS["text"])],
                  padding=[("selected", (22, 11)), ("!selected", (15, 7))],
                  font=[("selected", ("Segoe UI Semibold", 10)),
                        ("!selected", ("Segoe UI", 9))])
        style.configure("Vertical.TScrollbar", background=COLORS["surface2"],
                        troughcolor=COLORS["window"])
        style.configure("Treeview", background=COLORS["surface"],
                        fieldbackground=COLORS["surface"], foreground=COLORS["text"],
                        rowheight=28)
        style.map("Treeview", background=[("selected", COLORS["accent"])],
                  foreground=[("selected", "#ffffff")])

    def t(self, russian: str) -> str:
        return TEXT_EN.get(russian, russian) if self.language_code == "en" else russian

    @staticmethod
    def _asset(name: str) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS) / "assets" / name
        return Path(__file__).resolve().parent / "assets" / name

    def _build(self) -> None:
        self.root.title(APP_NAME)
        self.root.geometry("1180x820")
        self.root.minsize(980, 680)
        try:
            self.root.iconbitmap(default=str(self._asset("app-icon.ico")))
        except tk.TclError:
            pass

        shell = tk.Frame(self.root, bg=COLORS["window"])
        shell.pack(fill=tk.BOTH, expand=True)
        sidebar = tk.Frame(shell, bg=COLORS["sidebar"], width=218)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
        brand.pack(fill=tk.X, padx=18, pady=(20, 24))
        try:
            self.logo_icon = tk.PhotoImage(file=str(self._asset("app-icon-48.png")))
            tk.Label(brand, image=self.logo_icon, bg=COLORS["sidebar"]).pack(side=tk.LEFT)
        except tk.TclError:
            self.logo_icon = None
        brand_text = tk.Frame(brand, bg=COLORS["sidebar"])
        brand_text.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(brand_text, text="Universal SCS", bg=COLORS["sidebar"],
                 fg=COLORS["text"], font=("Segoe UI Semibold", 11)).pack(anchor=tk.W)
        tk.Label(brand_text, text="Packer", bg=COLORS["sidebar"],
                 fg=COLORS["accent"], font=("Segoe UI Semibold", 11)).pack(anchor=tk.W)

        self.current_page = "main"
        self.nav_buttons: dict[str, tk.Button] = {}
        for key, glyph, label in (("main", "▣", "Упаковка"),
                                  ("options", "⚙", "Параметры"),
                                  ("settings", "☰", "Настройки"),
                                  ("log", ">_", "Журнал"),
                                  ("help", "?", "Справка")):
            button = tk.Button(
                sidebar, text=f"  {glyph}   {label}", anchor=tk.W,
                bg=COLORS["sidebar"], fg=COLORS["muted"],
                activebackground=COLORS["surface2"], activeforeground=COLORS["text"],
                relief=tk.FLAT, bd=0, font=("Segoe UI Semibold", 10),
                padx=16, pady=11, command=lambda page=key: self.show_page(page))
            button.pack(fill=tk.X, padx=10, pady=2)
            self.nav_buttons[key] = button
        tk.Label(sidebar, text="v1 • Python", bg=COLORS["sidebar"],
                 fg="#76545b", font=("Segoe UI", 9)).pack(side=tk.BOTTOM, pady=18)

        right = ttk.Frame(shell, padding=(22, 17, 22, 14))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        top = ttk.Frame(right)
        top.pack(fill=tk.X, pady=(0, 13))
        title_box = ttk.Frame(top)
        title_box.pack(side=tk.LEFT)
        self.page_title = ttk.Label(title_box, style="Title.TLabel")
        self.page_title.pack(anchor=tk.W)
        self.page_subtitle = ttk.Label(title_box, style="Muted.TLabel")
        self.page_subtitle.pack(anchor=tk.W, pady=(2, 0))
        language = ttk.Frame(top)
        language.pack(side=tk.RIGHT, pady=4)
        ttk.Label(language, text=self.t("Язык"), style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(0, 7))
        self.top_language_combo = ttk.Combobox(
            language, textvariable=self.language_choice,
            values=("Русский", "English"), state="readonly", width=10,
            takefocus=False)
        self.top_language_combo.pack(side=tk.LEFT)
        self.top_language_combo.bind("<<ComboboxSelected>>", self.change_language)
        self._protect_combo_wheel(self.top_language_combo, scroll_options=False)
        self.page_host = ttk.Frame(right)
        self.page_host.pack(fill=tk.BOTH, expand=True)
        self.pages = {key: ttk.Frame(self.page_host)
                      for key in ("main", "options", "settings", "log", "help")}
        self.main_tab = self.pages["main"]
        self.options_tab = self.pages["options"]
        self.settings_tab = self.pages["settings"]
        self.log_tab = self.pages["log"]
        self.help_tab = self.pages["help"]
        self._build_main()
        self._build_options()
        self._build_settings()
        self._build_log()
        self._build_help()

        preview = ttk.Frame(right, style="Card.TFrame", padding=10)
        preview.pack(fill=tk.X, pady=(12, 8))
        ttk.Label(preview, text="КОМАНДА ЗАПУСКА",
                  style="Card.TLabel").pack(anchor=tk.W)
        row = ttk.Frame(preview, style="Card.TFrame")
        row.pack(fill=tk.X, pady=(5, 0))
        self.command_preview = tk.StringVar()
        ttk.Entry(row, textvariable=self.command_preview, state="readonly",
                  exportselection=False).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Копировать", command=self.copy_command).pack(
            side=tk.LEFT, padx=(8, 0))

        footer = ttk.Frame(right)
        footer.pack(fill=tk.X)
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side=tk.LEFT)
        self.stop_button = ttk.Button(footer, text="Остановить", command=self.stop,
                                      state=tk.DISABLED)
        self.stop_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.run_button = ttk.Button(footer, text="Упаковать", style="Accent.TButton",
                                     command=self.run)
        self.run_button.pack(side=tk.RIGHT)
        self.new_tab_button = ttk.Button(
            footer, text="Запустить в новой вкладке журнала",
            command=self.run_in_new_tab)
        self.new_tab_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.show_page("main")

    def _card(self, parent, title: str) -> ttk.Frame:
        card = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=12)
        card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        return card

    def _install_copy_support(self) -> None:
        """Provide layout-independent copying and a context menu everywhere."""
        self.copy_menu = tk.Menu(
            self.root, tearoff=False, bg=COLORS["surface2"], fg=COLORS["text"],
            activebackground=COLORS["accent"], activeforeground="#ffffff",
            relief=tk.FLAT)
        self.copy_menu.add_command(
            label=self.t("Копировать"), command=self._copy_context_widget)
        self.copy_menu.add_command(
            label=self.t("Выделить всё"), command=self._select_all_context_widget)
        self._copy_widget = None

        def show_menu(event) -> str:
            self._copy_widget = event.widget
            self.copy_menu.entryconfigure(0, label=self.t("Копировать"))
            self.copy_menu.entryconfigure(1, label=self.t("Выделить всё"))
            self.copy_menu.tk_popup(event.x_root, event.y_root)
            return "break"

        for widget_class in ("Text", "Entry", "TEntry", "Listbox", "Label", "TLabel"):
            self.root.bind_class(widget_class, "<Button-3>", show_menu, add="+")
        # On a Russian keyboard layout Tk reports a Cyrillic keysym for the
        # physical C key, so binding only <Control-c> is insufficient.
        self.root.bind_all("<Control-KeyPress>", self._copy_shortcut, add="+")

    def _widget_text(self, widget, selected_only: bool = False) -> str:
        try:
            if isinstance(widget, tk.Text):
                try:
                    return widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                except tk.TclError:
                    if widget in self.log_models and not selected_only:
                        return self._log_model_text(widget)
                    return "" if selected_only else widget.get("1.0", tk.END).rstrip()
            if isinstance(widget, tk.Listbox):
                selection = widget.curselection()
                if selection:
                    return "\n".join(str(widget.get(index)) for index in selection)
                return "" if selected_only else "\n".join(
                    str(widget.get(index)) for index in range(widget.size()))
            if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                try:
                    if widget.selection_present():
                        return widget.get()[widget.index(tk.SEL_FIRST):widget.index(tk.SEL_LAST)]
                except tk.TclError:
                    pass
                return "" if selected_only else str(widget.get())
            text = widget.cget("text")
            return str(text)
        except (AttributeError, tk.TclError):
            return ""

    def _set_clipboard(self, text: str) -> bool:
        if not text:
            return False
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.status.set(self.t("Текст скопирован"))
        return True

    def _copy_context_widget(self) -> None:
        if self._copy_widget is not None:
            self._set_clipboard(self._widget_text(self._copy_widget))

    def _select_all_context_widget(self) -> None:
        widget = self._copy_widget
        if isinstance(widget, tk.Text):
            widget.tag_add(tk.SEL, "1.0", "end-1c")
            widget.mark_set(tk.INSERT, "1.0")
        elif isinstance(widget, tk.Listbox):
            widget.selection_set(0, tk.END)
        elif isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            widget.selection_range(0, tk.END)
            widget.icursor(0)

    def _copy_shortcut(self, event) -> str | None:
        if event.keycode == 65 or str(getattr(event, "keysym", "")).casefold() == "a":
            self._copy_widget = self.root.focus_get()
            self._select_all_context_widget()
            return "break"
        if event.keycode != 67 and str(getattr(event, "keysym", "")).casefold() != "c":
            return None
        widget = self.root.focus_get()
        if widget is not None and self._set_clipboard(
                self._widget_text(widget, selected_only=True)):
            return "break"
        return None

    def show_page(self, page: str) -> None:
        for frame in self.pages.values():
            frame.pack_forget()
        self.pages[page].pack(fill=tk.BOTH, expand=True)
        self.current_page = page
        titles = {
            "main": ("Упаковка", "Подготовка одного или нескольких SCS-архивов"),
            "options": ("Параметры", "Формат, таблицы, пути и дополнительные настройки"),
            "settings": ("Настройки", "Параметры приложения, производительности и языка"),
            "log": ("Журнал", "События текущей операции"),
            "help": ("Справка", "Полный справочник параметров командной строки"),
        }
        title, subtitle = titles[page]
        if self.language_code == "en":
            title = TEXT_EN.get(title, title)
            subtitle = TEXT_EN.get(subtitle, subtitle)
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        for key, button in self.nav_buttons.items():
            active = key == page
            button.configure(bg=COLORS["surface2"] if active else COLORS["sidebar"],
                             fg=COLORS["text"] if active else COLORS["muted"])

    def change_language(self, _event=None) -> None:
        def release_language_selection() -> None:
            for combo in (getattr(self, "top_language_combo", None),
                          getattr(self, "settings_language_combo", None)):
                try:
                    if combo is not None:
                        combo.selection_clear()
                except tk.TclError:
                    pass
            try:
                self.root.focus_set()
            except tk.TclError:
                pass
        self.root.after_idle(release_language_selection)
        old = self.language_code
        self.language_code = "en" if self.language_choice.get() == "English" else "ru"
        if old == self.language_code:
            return
        self._app_settings["language"] = self.language_code
        save_app_settings(self._app_settings)
        mapping = TEXT_EN if self.language_code == "en" else {v: k for k, v in TEXT_EN.items()}

        self._render_options()
        self._translate_subtree(self.root, mapping)
        if hasattr(self, "activity_notebook"):
            self._refresh_activity_tabs()
            self._rerender_all_logs()
        self._filter_help()
        self.show_page(self.current_page)

    def _translate_subtree(self, widget, mapping: dict[str, str]) -> None:
        try:
            text = widget.cget("text")
            widget.configure(text=mapping.get(text, text))
        except (tk.TclError, TypeError):
            pass
        if isinstance(widget, ttk.Combobox):
            values = []
            for value in widget.cget("values"):
                values.append(mapping.get(value, value))
            widget.configure(values=values)
            current = widget.get()
            current = mapping.get(current, current)
            if current != widget.get():
                widget.set(current)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._translate_subtree(child, mapping)

    def _build_main(self) -> None:
        body = ttk.Frame(self.main_tab)
        body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left_outer = tk.Frame(body, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                              highlightthickness=1)
        left_outer.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6))
        ttk.Label(left_outer, text="ВХОДНЫЕ ПАПКИ И АРХИВЫ",
                  style="Section.TLabel").pack(anchor=tk.W, padx=14, pady=(12, 7))
        self.input_list = tk.Listbox(left_outer, bg=COLORS["console"], fg=COLORS["text"],
                                     selectbackground=COLORS["accent"], relief=tk.FLAT,
                                     font=("Segoe UI", 10), activestyle="none",
                                     exportselection=False)
        self.input_list.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        buttons = ttk.Frame(left_outer, style="Card.TFrame")
        buttons.pack(fill=tk.X, padx=14, pady=(0, 13))
        ttk.Button(buttons, text="+ Файлы", command=self.add_input_files).pack(side=tk.LEFT)
        ttk.Button(buttons, text="+ Папка", command=self.add_input_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Удалить", command=self.remove_input).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="↓", width=2,
                   command=lambda: self._move_selected(self.inputs, self.input_list, 1)).pack(side=tk.RIGHT, padx=3)
        ttk.Button(buttons, text="↑", width=2,
                   command=lambda: self._move_selected(self.inputs, self.input_list, -1)).pack(side=tk.RIGHT)

        right_outer = tk.Frame(body, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                               highlightthickness=1)
        right_outer.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0))
        ttk.Label(right_outer, text="ВЫХОДНЫЕ SCS-АРХИВЫ",
                  style="Section.TLabel").pack(anchor=tk.W, padx=14, pady=(12, 7))
        self.output_list = tk.Listbox(right_outer, bg=COLORS["console"], fg=COLORS["text"],
                                      selectbackground=COLORS["accent"], relief=tk.FLAT,
                                      font=("Segoe UI", 10), activestyle="none",
                                      exportselection=False)
        self.output_list.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        buttons = ttk.Frame(right_outer, style="Card.TFrame")
        buttons.pack(fill=tk.X, padx=14, pady=(0, 13))
        ttk.Button(buttons, text="+ Новый архив", command=self.add_output).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Обзор…", command=self.browse_output).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Удалить", command=self.remove_output).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="↓", width=2,
                   command=lambda: self._move_selected(self.outputs, self.output_list, 1)).pack(side=tk.RIGHT, padx=3)
        ttk.Button(buttons, text="↑", width=2,
                   command=lambda: self._move_selected(self.outputs, self.output_list, -1)).pack(side=tk.RIGHT)

        controls = self._card(self.main_tab, "ОБЩИЕ ПАРАМЕТРЫ ЗАПУСКА")
        controls.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            controls, text="Настроить каждый архив",
            variable=self.configure_each_archive).grid(
                row=0, column=0, sticky=tk.W, padx=(0, 24), pady=5)
        verbosity_choices = (("Минимальный", "minimal"),
                             ("С индикатором прогресса", "progress"),
                             ("Подробный — каждый файл", "detailed"))
        self._mapped_combo(
            controls, 1, "Подробность вывода", self.verbosity,
            verbosity_choices)

    def _field(self, parent, row: int, label: str, variable: tk.Variable,
               browse=None, values: tuple[str, ...] | None = None):
        ttk.Label(parent, text=self.t(label), style="Card.TLabel").grid(
            row=row, column=0, sticky=tk.W, pady=5, padx=(0, 12))
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.grid(row=row, column=1, sticky=tk.EW, pady=5)
        if values:
            widget = ttk.Combobox(holder, textvariable=variable, values=values,
                                  state="readonly")
            self._protect_combo_wheel(widget)
        else:
            widget = ttk.Entry(holder, textvariable=variable)
        widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if not values:
            ttk.Button(holder, text="×", width=2,
                       command=lambda: variable.set("")).pack(side=tk.LEFT, padx=(5, 0))
        if browse:
            ttk.Button(parent, text=self.t("Обзор…"), command=browse).grid(
                row=row, column=2, padx=(8, 0), pady=5)
        return widget

    def _protect_combo_wheel(self, combo: ttk.Combobox,
                             scroll_options: bool = True) -> None:
        def wheel(event) -> str:
            if (scroll_options and self.current_page == "options"
                    and hasattr(self, "options_canvas")):
                delta = getattr(event, "delta", 0)
                if delta:
                    self.options_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
                elif getattr(event, "num", 0) in (4, 5):
                    self.options_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
            return "break"
        combo.bind("<MouseWheel>", wheel)
        combo.bind("<Button-4>", wheel)
        combo.bind("<Button-5>", wheel)

        def release_selection(_event=None) -> None:
            def clear() -> None:
                try:
                    combo.selection_clear()
                except tk.TclError:
                    pass
                try:
                    self.root.focus_set()
                except tk.TclError:
                    pass
            self.root.after_idle(clear)
        combo.bind("<<ComboboxSelected>>", release_selection, add="+")
        combo.bind("<FocusOut>", lambda _event: combo.selection_clear(), add="+")

    def _build_options(self) -> None:
        canvas = tk.Canvas(self.options_tab, bg=COLORS["window"], highlightthickness=0)
        self.options_canvas = canvas
        scrollbar = ttk.Scrollbar(self.options_tab, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(12, 0))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(12, 0))
        self.options_content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.options_content, anchor=tk.NW)
        self.options_content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units") if self.current_page == "options" else None)

        self._render_options()

    def _mapped_combo(self, parent, row: int, label: str, target: tk.StringVar,
                      choices: tuple[tuple[str, str], ...]) -> None:
        if self.language_code == "en":
            label = TEXT_EN.get(label, label)
            choices = tuple((TEXT_EN.get(text, text), value) for text, value in choices)
        ttk.Label(parent, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky=tk.W, padx=(0, 12), pady=5)
        reverse = {value: text for text, value in choices}
        display = tk.StringVar(value=reverse.get(target.get(), choices[0][0]))
        combo = ttk.Combobox(parent, textvariable=display,
                             values=tuple(text for text, _value in choices),
                             state="readonly")
        combo.grid(row=row, column=1, sticky=tk.EW, pady=5)
        self._protect_combo_wheel(combo)
        combo.bind("<<ComboboxSelected>>", lambda _e: target.set(
            dict(choices)[display.get()]), add="+")
        def sync_display(*_args) -> None:
            wanted = reverse.get(target.get(), choices[0][0])
            if display.get() != wanted:
                display.set(wanted)
        trace_id = target.trace_add("write", sync_display)
        combo.bind("<Destroy>", lambda _event: target.trace_remove("write", trace_id),
                   add="+")

    def _global_settings(self) -> SimpleNamespace:
        cached = getattr(self, "_global_settings_model", None)
        if cached is not None:
            return cached
        settings = SimpleNamespace(
            file_system=self.file_system, compression=self.compression,
            salt=self.salt, v1_table_position=self.v1_table_position,
            v2_table_position=self.v2_table_position,
            v2_metadata_position=self.v2_metadata_position,
            v2_table_compressed=self.v2_table_compressed,
            v2_metadata_compressed=self.v2_metadata_compressed,
            encrypt_mode=self.encrypt_mode, encrypt_list=self.encrypt_list,
            hide_mode=self.hide_mode, hide_list=self.hide_list,
            force_hide=self.force_hide, exclude=self.exclude,
            reference=self.reference, empty_files=self.empty_files,
            directories=self.directories, entry_sort=self.entry_sort,
            sort_payload=self.sort_payload,
            dry_run=self.dry_run,
            junk_duplicate_hash=self.junk_duplicate_hash,
            junk_metadata_out_of_bounds=self.junk_metadata_out_of_bounds,
            junk_invalid_metadata=self.junk_invalid_metadata,
            junk_payload_out_of_bounds=self.junk_payload_out_of_bounds,
            junk_payload_overlaps_table=self.junk_payload_overlaps_table,
            junk_invalid_zlib_header=self.junk_invalid_zlib_header,
            junk_unknown_chunk=self.junk_unknown_chunk,
            junk_suspicious_compression=self.junk_suspicious_compression,
            junk_random_valid=self.junk_random_valid,
            junk_decoy_metadata_descriptor=self.junk_decoy_metadata_descriptor,
            junk_incorrect_eocd=self.junk_incorrect_eocd,
            junk_false_entries=self.junk_false_entries,
            crash_game_by_manifest=self.crash_game_by_manifest,
            global_settings=True)
        self._global_settings_model = settings
        return settings

    def _new_output_settings(self, output: str) -> SimpleNamespace:
        source = self._global_settings()
        settings = SimpleNamespace(
            file_system=tk.StringVar(value=source.file_system.get()),
            compression=tk.StringVar(value=source.compression.get()),
            salt=tk.StringVar(value=source.salt.get()),
            v1_table_position=tk.StringVar(value=source.v1_table_position.get()),
            v2_table_position=tk.StringVar(value=source.v2_table_position.get()),
            v2_metadata_position=tk.StringVar(value=source.v2_metadata_position.get()),
            v2_table_compressed=tk.BooleanVar(value=source.v2_table_compressed.get()),
            v2_metadata_compressed=tk.BooleanVar(value=source.v2_metadata_compressed.get()),
            encrypt_mode=tk.StringVar(value=source.encrypt_mode.get()),
            encrypt_list=tk.StringVar(value=source.encrypt_list.get()),
            hide_mode=tk.StringVar(value=source.hide_mode.get()),
            hide_list=tk.StringVar(value=source.hide_list.get()),
            force_hide=tk.StringVar(value=source.force_hide.get()),
            exclude=tk.StringVar(value=source.exclude.get()),
            reference=tk.StringVar(value=source.reference.get()),
            empty_files=tk.BooleanVar(value=source.empty_files.get()),
            directories=tk.BooleanVar(value=source.directories.get()),
            entry_sort=tk.BooleanVar(value=source.entry_sort.get()),
            sort_payload=tk.BooleanVar(value=source.sort_payload.get()),
            dry_run=tk.BooleanVar(value=source.dry_run.get()),
            junk_duplicate_hash=tk.StringVar(value=source.junk_duplicate_hash.get()),
            junk_metadata_out_of_bounds=tk.StringVar(value=source.junk_metadata_out_of_bounds.get()),
            junk_invalid_metadata=tk.StringVar(value=source.junk_invalid_metadata.get()),
            junk_payload_out_of_bounds=tk.StringVar(value=source.junk_payload_out_of_bounds.get()),
            junk_payload_overlaps_table=tk.StringVar(value=source.junk_payload_overlaps_table.get()),
            junk_invalid_zlib_header=tk.StringVar(value=source.junk_invalid_zlib_header.get()),
            junk_unknown_chunk=tk.StringVar(value=source.junk_unknown_chunk.get()),
            junk_suspicious_compression=tk.StringVar(value=source.junk_suspicious_compression.get()),
            junk_random_valid=tk.StringVar(value=source.junk_random_valid.get()),
            junk_decoy_metadata_descriptor=tk.BooleanVar(
                value=source.junk_decoy_metadata_descriptor.get()),
            junk_incorrect_eocd=tk.BooleanVar(
                value=source.junk_incorrect_eocd.get()),
            junk_false_entries=tk.StringVar(
                value=source.junk_false_entries.get()),
            crash_game_by_manifest=tk.BooleanVar(
                value=source.crash_game_by_manifest.get()),
            global_settings=False, output_key=output)
        variables = tuple(value for value in vars(settings).values()
                          if isinstance(value, tk.Variable))
        for variable in variables:
            variable.trace_add("write", lambda *_args: self.refresh_command())
        settings.file_system.trace_add(
            "write", lambda *_args, item=settings: self._output_file_system_changed(item))
        settings.hide_mode.trace_add(
            "write", lambda *_args, item=settings:
            self._update_settings_directory_availability(item))
        settings.compression.trace_add(
            "write", lambda *_args, item=settings:
            self._update_settings_compression_warning(item))
        return settings

    def _settings_for_output(self, output: str) -> SimpleNamespace:
        if output not in self.output_settings:
            self.output_settings[output] = self._new_output_settings(output)
        return self.output_settings[output]

    def _output_file_system_changed(self, settings: SimpleNamespace) -> None:
        current = settings.file_system.get().casefold()
        if current == "hashfsv1":
            settings.salt.set("0")
            settings.v1_table_position.set("1")
        elif current == "hashfsv2":
            settings.salt.set("random")
            settings.v2_table_position.set("2")
            settings.v2_metadata_position.set("2")
            settings.v2_table_compressed.set(True)
            settings.v2_metadata_compressed.set(True)
        self._render_output_settings(settings)
        self.refresh_command()

    def _render_output_settings(self, settings: SimpleNamespace) -> None:
        """Switch the already-created controls without rebuilding their tab."""
        self._apply_file_system_layout(settings)

    def _update_settings_compression_warning(self, settings: SimpleNamespace) -> None:
        warning = getattr(settings, "compression_warning", None)
        if warning is None:
            return
        try:
            warning.configure(
                text=self._compression_warning_text(settings, self.language_code))
        except tk.TclError:
            pass

    @staticmethod
    def _update_settings_directory_availability(settings: SimpleNamespace) -> None:
        checkbox = getattr(settings, "directory_checkbox", None)
        if checkbox is None:
            return
        disabled = (settings.file_system.get().casefold()
                    in ("hashfsv1", "hashfsv2")
                    and settings.hide_mode.get() != "none")
        try:
            checkbox.configure(state=tk.DISABLED if disabled else tk.NORMAL)
        except tk.TclError:
            pass

    @staticmethod
    def _compression_warning_text(settings: SimpleNamespace, language: str) -> str:
        if settings.compression.get() != "no":
            return ""
        russian = ("⚠ Игра нормально читает сжатые файлы; практической разницы "
                   "в работе почти нет. Режим no только заметно увеличивает архив.")
        return TEXT_EN.get(russian, russian) if language == "en" else russian

    def _render_options(self) -> None:
        if not hasattr(self, "options_content"):
            return
        for child in self.options_content.winfo_children():
            child.destroy()
        content = self.options_content
        if self.configure_each_archive.get() and self.outputs:
            for output in self.outputs:
                settings = self._settings_for_output(output)
                shell = ttk.Frame(content, style="Card.TFrame")
                shell.pack(fill=tk.X, pady=(0, 10))
                opened = self.output_fold_open.get(output, False)
                header = tk.Button(
                    shell, text=f"{'▼' if opened else '▶'}  {Path(output).name}",
                    anchor=tk.W, bg=COLORS["surface2"], fg=COLORS["text"],
                    activebackground=COLORS["border"], activeforeground=COLORS["text"],
                    relief=tk.FLAT, bd=0, padx=14, pady=10,
                    font=("Segoe UI Semibold", 10))
                header.pack(fill=tk.X)
                body = ttk.Frame(shell, padding=(10, 10, 10, 0))
                settings.option_body = body
                settings.option_header = header
                self._render_setting_cards(body, settings)
                if opened:
                    body.pack(fill=tk.X)

                def toggle(path=output, panel=body, button=header) -> None:
                    expanded = not self.output_fold_open.get(path, False)
                    self.output_fold_open[path] = expanded
                    if expanded:
                        panel.pack(fill=tk.X)
                    else:
                        panel.pack_forget()
                    button.configure(
                        text=f"{'▼' if expanded else '▶'}  {Path(path).name}")
                header.configure(command=toggle)
        else:
            self._render_setting_cards(content, self._global_settings())
        if self.language_code == "en":
            self._translate_subtree(content, TEXT_EN)

    def _render_setting_cards(self, content, settings: SimpleNamespace) -> None:
        archive = self._card(content, "ФОРМАТ АРХИВА")
        archive.columnconfigure(1, weight=1)
        self._field(archive, 0, "Файловая система  (--file-system)", settings.file_system,
                    values=("hashfsv2", "hashfsv1", "eZIP", "AEM"))
        positions = (("Обычный — offset 0x1000", "1"),
                     ("В самом начале", "3"), ("В конце", "2"),
                     ("Случайно в середине", "4"))
        hash_options = ttk.Frame(archive, style="Card.TFrame")
        hash_options.grid(row=1, column=0, columnspan=3, sticky=tk.EW)
        hash_options.columnconfigure(1, weight=1)
        self._field(hash_options, 0, "Метод сжатия", settings.compression,
                    values=("max", "medium", "min", "no"))
        warning = ttk.Label(
            hash_options, text=self._compression_warning_text(settings, self.language_code),
            style="Warning.Card.TLabel", justify=tk.LEFT, wraplength=680)
        warning.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(0, 5))
        settings.compression_warning = warning
        if settings.global_settings:
            self.compression_warning = warning
        self._field(hash_options, 2, "Соль хеширования", settings.salt)

        v1_options = ttk.Frame(hash_options, style="Card.TFrame")
        v1_options.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        v1_options.columnconfigure(1, weight=1)
        self._mapped_combo(v1_options, 0, "Положение таблицы",
                           settings.v1_table_position, positions)

        v2_options = ttk.Frame(hash_options, style="Card.TFrame")
        v2_options.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        v2_options.columnconfigure(0, weight=1)
        v2_options.columnconfigure(1, weight=1)
        table_group = ttk.LabelFrame(
            v2_options, text="Таблица путей", style="Card.TLabelframe", padding=10)
        table_group.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 5))
        table_group.columnconfigure(1, weight=1)
        self._mapped_combo(table_group, 0, "Положение",
                           settings.v2_table_position, positions)
        ttk.Checkbutton(table_group, text="Сжата",
                        variable=settings.v2_table_compressed).grid(
            row=1, column=1, sticky=tk.W, pady=5)
        metadata_group = ttk.LabelFrame(
            v2_options, text="Метаданные", style="Card.TLabelframe", padding=10)
        metadata_group.grid(row=0, column=1, sticky=tk.NSEW, padx=(5, 0))
        metadata_group.columnconfigure(1, weight=1)
        self._mapped_combo(metadata_group, 0, "Положение",
                           settings.v2_metadata_position, positions)
        ttk.Checkbutton(metadata_group, text="Сжаты",
                        variable=settings.v2_metadata_compressed).grid(
            row=1, column=1, sticky=tk.W, pady=5)
        ttk.Button(v2_options, text="🎲 Рандомизировать таблицы",
                   command=lambda item=settings: self.randomize_v2_tables(item)).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(7, 3))

        paths = self._card(content, "СПИСКИ ПУТЕЙ И ЗАВИСИМОСТИ")
        paths.columnconfigure(1, weight=1)
        self._selector_row(
            paths, 0, "Шифрование SII  (-e)", settings.encrypt_mode,
            settings.encrypt_list,
            lambda item=settings: self._choose_selector_list(
                item.encrypt_list, item.encrypt_mode))
        self._selector_row(
            paths, 1, "Скрытие путей  (-h)", settings.hide_mode,
            settings.hide_list,
            lambda item=settings: self._choose_selector_list(
                item.hide_list, item.hide_mode))
        self._field(paths, 2, "Файл списка принудительного скрытия (.txt)  (-fh)",
                    settings.force_hide,
                    lambda item=settings: self.choose_text_file(item.force_hide))
        self._field(paths, 3, "Исключить по списку  (-x)", settings.exclude,
                    lambda item=settings: self.choose_text_file(item.exclude))
        self._field(paths, 4, "Reference  (-ref)", settings.reference,
                    lambda item=settings: self.choose_source(item.reference))

        extra = self._card(content, "ДОПОЛНИТЕЛЬНЫЕ ПАРАМЕТРЫ")
        ttk.Checkbutton(extra, text="Сохранять пустые файлы  (-af)",
                        variable=settings.empty_files).grid(row=0, column=0, sticky=tk.W, pady=5)
        directory_checkbox = ttk.Checkbutton(
            extra, text="Сохранять пустые каталоги  (-ad)",
            variable=settings.directories)
        directory_checkbox.grid(
            row=0, column=1, sticky=tk.W, padx=28, pady=5)
        settings.directory_checkbox = directory_checkbox
        if settings.global_settings:
            self.directory_checkbox = directory_checkbox
        entry_sort_checkbox = ttk.Checkbutton(
            extra, text="Сортировать записи  (-es=y)", variable=settings.entry_sort)
        entry_sort_checkbox.grid(row=1, column=0, sticky=tk.W, pady=5)
        payload_sort_checkbox = ttk.Checkbutton(
            extra, text=self.t("Перемешивать payload  (-spl=y)"), variable=settings.sort_payload)
        payload_sort_checkbox.grid(row=1, column=1, sticky=tk.W, padx=28, pady=5)
        dry_run_checkbox = ttk.Checkbutton(
            extra, text="Проверка без записи  (--dry-run)", variable=settings.dry_run)
        dry_run_checkbox.grid(row=1, column=0, sticky=tk.W, pady=5)

        fun = self._card(content, "РАЗВЛЕЧЕНИЕ")
        fun.columnconfigure(0, weight=1)
        junk_section = ttk.LabelFrame(
            fun, text=self.t("МУСОРНЫЕ ЗАПИСИ (АНАЛОГ GMC PACKER)"),
            style="Card.TLabelframe", padding=10)
        junk_section.grid(row=0, column=0, sticky=tk.EW)
        junk_section.columnconfigure(1, weight=1)
        junk_fields = (
            ("Дубликаты хэша", settings.junk_duplicate_hash),
            ("Метаданные за границами таблицы", settings.junk_metadata_out_of_bounds),
            ("Некорректные метаданные", settings.junk_invalid_metadata),
            ("Данные за границами архива", settings.junk_payload_out_of_bounds),
            ("Данные перекрывают таблицу", settings.junk_payload_overlaps_table),
            ("Некорректный заголовок zlib", settings.junk_invalid_zlib_header),
            ("Неизвестный тип metadata-блока (только v2)", settings.junk_unknown_chunk),
            ("Подозрительное сжатие", settings.junk_suspicious_compression),
            ("Случайные корректные записи", settings.junk_random_valid),
        )
        unknown_chunk_widget = None
        for row, (label, variable) in enumerate(junk_fields):
            widget = self._field(junk_section, row, label, variable)
            if variable is settings.junk_unknown_chunk:
                unknown_chunk_widget = widget
        decoy_descriptor_checkbox = ttk.Checkbutton(
            junk_section, text=self.t(
                "Ложный metadata-дескриптор перед настоящим, только HashFS v2  (DMD=1)"),
            variable=settings.junk_decoy_metadata_descriptor)
        decoy_descriptor_checkbox.grid(
            row=len(junk_fields), column=0, columnspan=2, sticky=tk.W, pady=(5, 2))
        ttk.Label(
            junk_section,
            text=("HashFS v2: дубликаты 0 или 2. HashFS v1: до 50000. "
                  "Подозрительное сжатие: до 50000; случайные корректные: "
                  "до 1000; остальные категории: до 30."),
            style="Muted.TLabel", wraplength=720, justify=tk.LEFT).grid(
                row=len(junk_fields) + 1, column=0, columnspan=3,
                sticky=tk.EW, pady=(8, 4))
        ttk.Button(
            junk_section, text="🎲 Рандомизировать значения",
            command=lambda item=settings: self.randomize_junk_values(item)).grid(
            row=len(junk_fields) + 2, column=1, sticky=tk.W, pady=(4, 0))
        hash_junk_widgets = [
            widget
            for row in range(len(junk_fields) + 3)
            for widget in junk_section.grid_slaves(row=row)
        ]
        incorrect_eocd_checkbox = ttk.Checkbutton(
            junk_section,
            text=self.t("\u0423\u043a\u0430\u0437\u0430\u0442\u044c EOCD \u043d\u0435\u043f\u0440\u0430\u0432\u0438\u043b\u044c\u043d\u043e  (EOCD=1)"),
            variable=settings.junk_incorrect_eocd)
        incorrect_eocd_checkbox.grid(
            row=len(junk_fields) + 3, column=0, columnspan=2,
            sticky=tk.W, pady=(5, 2))
        false_entries_row = len(junk_fields) + 4
        self._field(
            junk_section, false_entries_row,
            self.t("\u041b\u043e\u0436\u043d\u044b\u0435 ZIP entries, \u0434\u043e 10000  (FE=N)"),
            settings.junk_false_entries)
        false_entries_widgets = list(junk_section.grid_slaves(row=false_entries_row))
        zip_randomize_row = false_entries_row + 1
        ttk.Button(
            junk_section, text=self.t("🎲 Рандомизировать значения"),
            command=lambda item=settings: self.randomize_zip_junk_values(item)).grid(
                row=zip_randomize_row, column=1, sticky=tk.W, pady=(4, 0))
        false_entries_widgets.extend(junk_section.grid_slaves(row=zip_randomize_row))

        break_card = ttk.LabelFrame(
            fun, text=self.t("СЛОМАТЬ ИГРУ"), style="Card.TLabelframe", padding=10)
        break_card.grid(row=1, column=0, sticky=tk.EW, pady=(12, 0))
        break_game_checkbox = ttk.Checkbutton(
            break_card,
            text=self.t("Сделать игру незапускаемой  (-cg=BM)"),
            variable=settings.crash_game_by_manifest)
        break_game_checkbox.grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Label(
            break_card,
            text=self.t(
                "Размеры compressed и uncompressed для manifest.sii будут намеренно "
                "завышены. Игра с таким модом больше не запустится."),
            style="Muted.TLabel", wraplength=720, justify=tk.LEFT).grid(
                row=1, column=0, sticky=tk.EW, pady=(2, 4))

        settings.hash_options = hash_options
        settings.v1_options = v1_options
        settings.v2_options = v2_options
        settings.paths_card = paths
        settings.extra_card = extra
        settings.fun_card = fun
        settings.break_card = break_card
        settings.entry_sort_checkbox = entry_sort_checkbox
        settings.payload_sort_checkbox = payload_sort_checkbox
        settings.decoy_descriptor_checkbox = decoy_descriptor_checkbox
        settings.incorrect_eocd_checkbox = incorrect_eocd_checkbox
        settings.false_entries_widgets = false_entries_widgets
        settings.hash_junk_widgets = hash_junk_widgets
        settings.break_game_checkbox = break_game_checkbox
        settings.unknown_chunk_widget = unknown_chunk_widget
        settings.dry_run_checkbox = dry_run_checkbox
        self._apply_file_system_layout(settings)

    def _apply_file_system_layout(self, settings: SimpleNamespace) -> None:
        """Show format-specific controls without destroying or recreating widgets."""
        required = ("hash_options", "v1_options", "v2_options", "paths_card",
                    "extra_card", "fun_card", "break_card", "entry_sort_checkbox",
                    "payload_sort_checkbox", "decoy_descriptor_checkbox",
                    "incorrect_eocd_checkbox", "hash_junk_widgets",
                    "false_entries_widgets",
                    "break_game_checkbox", "unknown_chunk_widget",
                    "dry_run_checkbox")
        if any(not hasattr(settings, name) for name in required):
            return
        fs = settings.file_system.get().casefold()
        hashfs = fs in ("hashfsv1", "hashfsv2")
        try:
            if hashfs:
                settings.hash_options.grid()
                settings.paths_card.pack(
                    fill=tk.BOTH, expand=True, pady=(0, 12),
                    before=settings.extra_card)
                settings.fun_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
                for widget in settings.hash_junk_widgets:
                    widget.grid()
                settings.incorrect_eocd_checkbox.grid_remove()
                for widget in settings.false_entries_widgets:
                    widget.grid_remove()
                settings.entry_sort_checkbox.grid_remove()
                settings.payload_sort_checkbox.grid()
                settings.dry_run_checkbox.grid()
                if fs == "hashfsv1":
                    settings.junk_unknown_chunk.set("0")
                    settings.junk_decoy_metadata_descriptor.set(False)
                    settings.decoy_descriptor_checkbox.configure(state=tk.DISABLED)
                    settings.unknown_chunk_widget.configure(state=tk.DISABLED)
                    settings.break_game_checkbox.configure(state=tk.NORMAL)
                    settings.break_card.grid()
                    settings.v2_options.grid_remove()
                    settings.v1_options.grid()
                else:
                    settings.decoy_descriptor_checkbox.configure(state=tk.NORMAL)
                    settings.unknown_chunk_widget.configure(state=tk.NORMAL)
                    settings.break_game_checkbox.configure(state=tk.DISABLED)
                    settings.break_card.grid_remove()
                    settings.v1_options.grid_remove()
                    settings.v2_options.grid()
            else:
                settings.hash_options.grid_remove()
                settings.paths_card.pack_forget()
                settings.fun_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
                for widget in settings.hash_junk_widgets:
                    widget.grid_remove()
                settings.incorrect_eocd_checkbox.grid()
                for widget in settings.false_entries_widgets:
                    widget.grid()
                settings.break_card.grid_remove()
                settings.dry_run_checkbox.grid_remove()
                settings.entry_sort_checkbox.grid()
                settings.payload_sort_checkbox.grid_remove()
            self._update_settings_directory_availability(settings)
        except tk.TclError:
            pass

    def _selector_row(self, parent, row, label, mode, value, browse) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky=tk.W, pady=5, padx=(0, 12))
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=5)
        choices = (("Не использовать", "none"), ("Все файлы", "all"),
                   ("Список путей", "list"))
        if self.language_code == "en":
            choices = tuple((TEXT_EN.get(text, text), key) for text, key in choices)
        reverse = {key: text for text, key in choices}
        display = tk.StringVar(value=reverse[mode.get()])
        combo = ttk.Combobox(holder, textvariable=display,
                             values=tuple(text for text, _key in choices),
                             state="readonly", width=16)
        combo.pack(side=tk.LEFT)
        self._protect_combo_wheel(combo)
        details = ttk.Frame(holder, style="Card.TFrame")
        details.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def render_details() -> None:
            for child in details.winfo_children():
                child.destroy()
            if mode.get() != "list":
                return
            ttk.Entry(details, textvariable=value).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 5))
            ttk.Button(details, text="×", width=2,
                       command=lambda: value.set("")).pack(side=tk.LEFT)
            ttk.Button(details, text=self.t("Обзор…"), command=browse).pack(
                side=tk.LEFT, padx=(5, 0))

        def selected(_event=None) -> None:
            mode.set(dict(choices)[display.get()])
            render_details()

        combo.bind("<<ComboboxSelected>>", selected, add="+")
        render_details()

    def _update_compression_warning(self, *_args) -> None:
        if not hasattr(self, "compression_warning"):
            return
        warning = ("⚠ Игра нормально читает сжатые файлы; практической разницы "
                   "в работе почти нет. Режим no только заметно увеличивает архив.")
        try:
            self.compression_warning.configure(
                text=self.t(warning) if self.compression.get() == "no" else "")
        except tk.TclError:
            pass

    def _update_directory_availability(self, *_args) -> None:
        if not hasattr(self, "directory_checkbox"):
            return
        hashfs_hiding = (self.file_system.get().casefold() in ("hashfsv1", "hashfsv2")
                         and self.hide_mode.get() != "none")
        try:
            self.directory_checkbox.configure(
                state=tk.DISABLED if hashfs_hiding else tk.NORMAL)
        except tk.TclError:
            pass

    def randomize_v2_tables(self, settings: SimpleNamespace | None = None) -> None:
        settings = settings or self._global_settings()
        settings.v2_table_position.set(str(random.randint(1, 4)))
        settings.v2_metadata_position.set(str(random.randint(1, 4)))
        settings.v2_table_compressed.set(bool(random.getrandbits(1)))
        settings.v2_metadata_compressed.set(bool(random.getrandbits(1)))

    def randomize_junk_values(self, settings: SimpleNamespace | None = None) -> None:
        settings = settings or self._global_settings()
        duplicate_limit = (2 if settings.file_system.get().casefold() == "hashfsv2"
                           else 50_000)
        duplicate = random.randint(0, duplicate_limit)
        if duplicate == 1:
            duplicate = 2
        settings.junk_duplicate_hash.set(str(duplicate))
        settings.junk_metadata_out_of_bounds.set(str(random.randint(0, 30)))
        settings.junk_invalid_metadata.set(str(random.randint(0, 30)))
        settings.junk_payload_out_of_bounds.set(str(random.randint(0, 30)))
        settings.junk_payload_overlaps_table.set(str(random.randint(0, 30)))
        settings.junk_invalid_zlib_header.set(str(random.randint(0, 30)))
        settings.junk_unknown_chunk.set(str(random.randint(0, 100)))
        settings.junk_suspicious_compression.set(str(random.randint(0, 50_000)))
        settings.junk_random_valid.set(str(random.randint(0, 1000)))
        settings.junk_decoy_metadata_descriptor.set(bool(random.getrandbits(1)))

    def randomize_zip_junk_values(self, settings: SimpleNamespace | None = None) -> None:
        settings = settings or self._global_settings()
        settings.junk_false_entries.set(str(random.randint(0, 10_000)))

    def _build_settings(self) -> None:
        content = ttk.Frame(self.settings_tab)
        content.pack(fill=tk.BOTH, expand=True)

        performance = self._card(content, self.t("ПРОИЗВОДИТЕЛЬНОСТЬ"))
        performance.columnconfigure(1, weight=1)
        ttk.Label(performance, text=self.t("Максимальное количество потоков"),
                  style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=5)
        threads = ttk.Spinbox(performance, from_=1, to=256,
                              textvariable=self.max_threads, width=12)
        threads.grid(row=0, column=1, sticky=tk.W, padx=(18, 0), pady=5)
        self._protect_combo_wheel(threads, scroll_options=False)

        interface = self._card(content, self.t("ИНТЕРФЕЙС"))
        ttk.Label(interface, text=self.t("Язык"), style="Card.TLabel").pack(
            anchor=tk.W, pady=(0, 5))
        self.settings_language_combo = ttk.Combobox(
            interface, textvariable=self.language_choice,
            values=("Русский", "English"), state="readonly", width=18,
            takefocus=False)
        self.settings_language_combo.pack(anchor=tk.W)
        self.settings_language_combo.bind("<<ComboboxSelected>>", self.change_language)
        self._protect_combo_wheel(self.settings_language_combo, scroll_options=False)
        ttk.Button(content, text=self.t("Сохранить настройки"),
                   style="Accent.TButton", command=lambda: self._save_app_preferences(True)).pack(
                       anchor=tk.E, pady=(2, 0))

    def _save_app_preferences(self, notify: bool = False) -> None:
        def number(value: str, fallback: int) -> int:
            try:
                return max(0, int(value.strip()))
            except ValueError:
                return fallback
        self._app_settings.update({
            "language": self.language_code,
            "max_threads": max(1, number(self.max_threads.get(), 1)),
        })
        self.max_threads.set(str(self._app_settings["max_threads"]))
        save_app_settings(self._app_settings)
        if notify:
            self.status.set(self.t("Настройки сохранены"))

    def _build_log(self) -> None:
        toolbar = ttk.Frame(self.log_tab)
        toolbar.pack(fill=tk.X, pady=(12, 8))
        ttk.Label(toolbar, text="Вывод текущей операции", style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="+ Новая вкладка", command=self.new_activity_tab).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Закрыть вкладку", command=self.close_activity_tab).pack(
            side=tk.RIGHT, padx=6)
        ttk.Button(toolbar, text="Очистить", command=self.clear_log).pack(side=tk.RIGHT, padx=6)
        ttk.Button(toolbar, text="Копировать", command=self.copy_log).pack(side=tk.RIGHT, padx=6)
        self.activity_notebook = ttk.Notebook(self.log_tab)
        self.activity_notebook.pack(fill=tk.BOTH, expand=True)
        self.activity_notebook.bind("<<NotebookTabChanged>>",
                                    self._activity_tab_changed)
        self.activity_logs: list[tk.Text] = []
        self.new_activity_tab()

    def new_activity_tab(self) -> tk.Text:
        frame = ttk.Frame(self.activity_notebook)
        log = tk.Text(frame, bg=COLORS["console"], fg=COLORS["text"],
                      insertbackground=COLORS["text"], relief=tk.FLAT,
                      font=("Cascadia Mono", 9), wrap=tk.WORD, padx=12, pady=10,
                      exportselection=False)
        scrollbar = ttk.Scrollbar(frame, command=log.yview)
        log.configure(yscrollcommand=scrollbar.set)
        log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in (("success", COLORS["success"]), ("warning", COLORS["warning"]),
                           ("error", COLORS["error"]), ("info", COLORS["info"]),
                           ("debug", COLORS["debug"])):
            log.tag_configure(tag, foreground=color, font=("Cascadia Mono", 9, "bold"))
        self.activity_logs.append(log)
        self.log_models[log] = []
        tab_word = "Activity" if self.language_code == "en" else "Журнал"
        self.activity_notebook.add(frame, text=f"{tab_word} {len(self.activity_logs)}")
        self.activity_notebook.select(frame)
        self._refresh_activity_tabs()
        return log

    def _activity_tab_changed(self, _event=None) -> None:
        self._refresh_activity_tabs()
        self._update_process_buttons()

    def _refresh_activity_tabs(self) -> None:
        if not hasattr(self, "activity_notebook"):
            return
        selected = self.activity_notebook.select()
        word = "Activity" if self.language_code == "en" else "Журнал"
        running_logs = {operation.get("log") for operation in self.operations.values()}
        for index, log in enumerate(self.activity_logs, 1):
            frame = str(log.master)
            running = log in running_logs
            marker = "● " if running else ""
            self.activity_notebook.tab(
                frame, text=f"{marker}{word} {index}")

    def close_activity_tab(self) -> None:
        log = self.active_log()
        if any(operation.get("log") is log for operation in self.operations.values()):
            messagebox.showwarning(
                APP_NAME,
                self.t("Нельзя закрыть вкладку, пока в ней выполняется упаковка."))
            return
        frame = log.master
        self.activity_notebook.forget(frame)
        self.activity_logs.remove(log)
        self.log_models.pop(log, None)
        frame.destroy()
        if not self.activity_logs:
            self.new_activity_tab()
        else:
            self._refresh_activity_tabs()
            self._update_process_buttons()

    def active_log(self) -> tk.Text:
        selected = self.activity_notebook.select()
        for log in self.activity_logs:
            if str(log.master) == selected:
                return log
        return self.activity_logs[-1]

    def clear_log(self) -> None:
        log = self.active_log()
        log.delete("1.0", tk.END)
        self.log_models[log].clear()
        for operation in self.operations.values():
            if operation.get("log") is log:
                operation.pop("progress_tag", None)
                operation.pop("progress_record", None)
                operation["write_lines"] = []
                operation["group_kind"] = None

    def _build_help(self) -> None:
        top = ttk.Frame(self.help_tab)
        top.pack(fill=tk.X, pady=(12, 8))
        ttk.Label(top, text="Полная справка по параметрам",
                  style="Title.TLabel").pack(side=tk.LEFT)
        self.help_search = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.help_search, width=34)
        search.pack(side=tk.RIGHT)
        ttk.Button(top, text="Копировать текст", command=self.copy_help).pack(
            side=tk.RIGHT, padx=(0, 8))
        self.help_search.trace_add("write", lambda *_args: self._filter_help())
        pane = ttk.Panedwindow(self.help_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = tk.Frame(pane, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        pane.add(left, weight=1)
        pane.add(right, weight=3)
        self.help_list = tk.Listbox(left, bg=COLORS["console"], fg=COLORS["text"],
                                    selectbackground=COLORS["accent"], relief=tk.FLAT,
                                    font=("Cascadia Mono", 10), activestyle="none",
                                    exportselection=False)
        self.help_list.pack(fill=tk.BOTH, expand=True)
        self.help_list.bind("<<ListboxSelect>>", self._show_help)
        self.help_detail = tk.Text(right, bg=COLORS["surface"], fg=COLORS["text"],
                                   relief=tk.FLAT, wrap=tk.WORD, padx=22, pady=20,
                                   font=("Segoe UI", 11), exportselection=False)
        detail_scroll = ttk.Scrollbar(right, command=self.help_detail.yview)
        self.help_detail.configure(yscrollcommand=detail_scroll.set)
        self.help_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.help_detail.tag_configure("title", font=("Segoe UI Semibold", 17),
                                       foreground=COLORS["accent"])
        self.help_detail.tag_configure("heading", font=("Segoe UI Semibold", 11),
                                       foreground=COLORS["text"], spacing1=14, spacing3=5)
        self.help_detail.tag_configure("code", font=("Cascadia Mono", 10),
                                       foreground=COLORS["info"])
        self._filtered_help = list(HELP_OPTIONS)
        self._filter_help()

    def _filter_help(self) -> None:
        query = self.help_search.get().casefold().strip()
        self._filtered_help = [item for item in HELP_OPTIONS
                               if not query or query in (" ".join(item) + " "
                                  + HELP_EN.get(item[0], "")).casefold()]
        self.help_list.delete(0, tk.END)
        for syntax, _description, _example in self._filtered_help:
            self.help_list.insert(tk.END, TEXT_EN.get(syntax, syntax)
                                  if self.language_code == "en" else syntax)
        if self._filtered_help:
            self.help_list.selection_set(0)
            self._show_help()
        else:
            self.help_detail.configure(state=tk.NORMAL)
            self.help_detail.delete("1.0", tk.END)
            self.help_detail.insert(tk.END, "Nothing found" if self.language_code == "en"
                                    else "Ничего не найдено")
            self.help_detail.configure(state=tk.DISABLED)

    def _show_help(self, _event=None) -> None:
        selected = self.help_list.curselection()
        if not selected:
            return
        syntax, description, example = self._filtered_help[selected[0]]
        if self.language_code == "en":
            description = DETAILED_HELP_EN.get(
                syntax, HELP_EN.get(syntax, description))
        else:
            description = DETAILED_HELP_RU.get(syntax, description)
        self.help_detail.configure(state=tk.NORMAL)
        self.help_detail.delete("1.0", tk.END)
        title = TEXT_EN.get(syntax, syntax) if self.language_code == "en" else syntax
        self.help_detail.insert(tk.END, title + "\n", "title")
        self.help_detail.insert(tk.END, "Description\n" if self.language_code == "en"
                                else "Описание\n", "heading")
        self.help_detail.insert(tk.END, description + "\n")
        self.help_detail.insert(tk.END, "Example\n" if self.language_code == "en"
                                else "Пример\n", "heading")
        self.help_detail.insert(tk.END, ".\\UniversalSCSPacker.exe " + example, "code")
        self.help_detail.configure(state=tk.DISABLED)

    def copy_help(self) -> None:
        try:
            text = self.help_detail.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            text = self.help_detail.get("1.0", tk.END).rstrip()
        self._set_clipboard(text)

    def add_input_files(self) -> None:
        paths = filedialog.askopenfilenames(title=self.t("Выберите входные архивы"),
                                            filetypes=((self.t("Архивы"), "*.scs *.zip *.7z *.rar *.tar *.gz"),
                                                       (self.t("Все файлы"), "*.*")))
        self._append_unique(self.inputs, paths, self.input_list)

    def add_input_folder(self) -> None:
        path = filedialog.askdirectory(title=self.t("Выберите входную папку"))
        if path:
            self._append_unique(self.inputs, (path,), self.input_list)

    def add_output(self) -> None:
        # Both language selectors share this variable.  Reading it directly
        # ensures that a newly opened dialog follows an immediately preceding
        # dynamic language change.
        english = self.language_choice.get() == "English"

        def local(russian: str, translated: str) -> str:
            return translated if english else russian

        dialog = tk.Toplevel(self.root)
        dialog.title(local("Новый выходной архив", "New output archive"))
        dialog.configure(bg=COLORS["window"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        name = tk.StringVar(value="archive.scs")
        folder = tk.StringVar(value=str(Path.cwd()))
        card = ttk.LabelFrame(dialog, text=local("Выходной архив", "Output archive"),
                              style="Card.TLabelframe", padding=14)
        card.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=local("Имя файла", "File name"), style="Card.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10), pady=6)
        filename = ttk.Entry(card, textvariable=name, width=45)
        filename.grid(row=0, column=1, columnspan=2, sticky=tk.EW, pady=6)
        ttk.Label(card, text=local("Папка", "Folder"), style="Card.TLabel").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 10), pady=6)
        ttk.Entry(card, textvariable=folder).grid(
            row=1, column=1, sticky=tk.EW, pady=6)

        def choose_folder() -> None:
            selected = filedialog.askdirectory(
                parent=dialog,
                title=local("Выберите папку назначения", "Choose destination folder"),
                initialdir=folder.get() or str(Path.cwd()))
            if selected:
                folder.set(selected)

        ttk.Button(card, text=local("Обзор…", "Browse…"), command=choose_folder).grid(
            row=1, column=2, padx=(8, 0), pady=6)
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=2, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))

        def create() -> None:
            filename_value = name.get().strip()
            if not filename_value:
                messagebox.showerror(
                    APP_NAME, local("Введите имя файла.", "Enter a file name."),
                    parent=dialog)
                return
            path = Path(filename_value)
            if not path.suffix:
                path = path.with_suffix(".scs")
            if not path.is_absolute():
                path = Path(folder.get().strip() or Path.cwd()) / path
            self._append_unique(self.outputs, (str(path),), self.output_list)
            dialog.destroy()

        ttk.Button(actions, text=local("Отмена", "Cancel"), command=dialog.destroy).pack(
            side=tk.RIGHT)
        ttk.Button(actions, text=local("Создать", "Create"), style="Accent.TButton",
                   command=create).pack(side=tk.RIGHT, padx=(0, 8))
        filename.focus_set()
        filename.selection_range(0, tk.END)
        dialog.bind("<Return>", lambda _event: create())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def browse_output(self) -> None:
        paths = filedialog.askopenfilenames(
            title=self.t("Выберите выходные архивы"),
            filetypes=((self.t("SCS-архив"), "*.scs"),
                       (self.t("Все файлы"), "*.*")))
        self._append_unique(self.outputs, paths, self.output_list)

    def _configure_each_changed(self, *_args) -> None:
        if not self.configure_each_archive.get() and self.outputs:
            # The last output becomes the single/global configuration shown on
            # the Parameters page and used by the command preview.
            source = self._settings_for_output(self.outputs[-1])
            target = self._global_settings()
            names = (
                "compression", "salt", "v1_table_position",
                "v2_table_position", "v2_metadata_position",
                "v2_table_compressed", "v2_metadata_compressed",
                "encrypt_mode", "encrypt_list", "hide_mode", "hide_list",
                "force_hide", "exclude", "reference", "empty_files",
                "directories", "entry_sort", "sort_payload", "dry_run",
                "junk_duplicate_hash", "junk_metadata_out_of_bounds",
                "junk_invalid_metadata", "junk_payload_out_of_bounds",
                "junk_payload_overlaps_table", "junk_invalid_zlib_header",
                "junk_unknown_chunk",
                "junk_suspicious_compression", "junk_random_valid",
                "junk_decoy_metadata_descriptor", "junk_incorrect_eocd",
                "junk_false_entries",
                "crash_game_by_manifest")
            target.file_system.set(source.file_system.get())
            for name in names:
                getattr(target, name).set(getattr(source, name).get())
        self._render_options()
        self.refresh_command()

    def _append_unique(self, target: list[str], values, widget: tk.Listbox) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)
                widget.insert(tk.END, value)
        if target is self.outputs and self.configure_each_archive.get():
            self._render_options()
        self.refresh_command()

    def _remove_selected(self, target: list[str], widget: tk.Listbox) -> None:
        for index in reversed(widget.curselection()):
            del target[index]
            widget.delete(index)
        if target is self.outputs and self.configure_each_archive.get():
            self._render_options()
        self.refresh_command()

    def _move_selected(self, target: list[str], widget: tk.Listbox, direction: int) -> None:
        selected = widget.curselection()
        if len(selected) != 1:
            return
        old = selected[0]
        new = old + direction
        if not 0 <= new < len(target):
            return
        target[old], target[new] = target[new], target[old]
        value = widget.get(old)
        widget.delete(old)
        widget.insert(new, value)
        widget.selection_set(new)
        widget.see(new)
        if target is self.outputs and self.configure_each_archive.get():
            self._render_options()
        self.refresh_command()

    def remove_input(self) -> None:
        self._remove_selected(self.inputs, self.input_list)

    def remove_output(self) -> None:
        self._remove_selected(self.outputs, self.output_list)

    def choose_text_file(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title=self.t("Выберите список путей"),
                                          filetypes=((self.t("Текстовые файлы"), "*.txt"),
                                                     (self.t("Все файлы"), "*.*")))
        if path:
            variable.set(path)

    def _choose_selector_list(self, value: tk.StringVar,
                              mode: tk.StringVar) -> None:
        self.choose_text_file(value)
        if value.get():
            mode.set("list")

    def choose_source(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(
            title=("Выберите папку reference (Отмена — выбрать архив)"
                   if self.language_code == "ru"
                   else "Choose reference folder (Cancel to choose an archive)"))
        if path:
            variable.set(path)
            return
        path = filedialog.askopenfilename(title=self.t("Выберите reference-архив"),
                                          filetypes=((self.t("Архивы"), "*.scs *.zip *.7z *.rar *.tar *.gz"),
                                                     (self.t("Все файлы"), "*.*")))
        if path:
            variable.set(path)

    def choose_encrypt_list(self) -> None:
        self.choose_text_file(self.encrypt_list)
        if self.encrypt_list.get():
            self.encrypt_mode.set("list")

    def choose_hide_list(self) -> None:
        self.choose_text_file(self.hide_list)
        if self.hide_list.get():
            self.hide_mode.set("list")

    def _file_system_changed(self, *_args) -> None:
        current = self.file_system.get().casefold()
        previous = self._last_file_system.casefold()
        if current == previous:
            return
        if current == "hashfsv1":
            self.salt.set("0")
            self.v1_table_position.set("1")
        elif current == "hashfsv2":
            self.salt.set("random")
            self.v2_table_position.set("2")
            self.v2_metadata_position.set("2")
            self.v2_table_compressed.set(True)
            self.v2_metadata_compressed.set(True)
        self._last_file_system = current
        self._apply_file_system_layout(self._global_settings())
        self.refresh_command()

    def _settings_arguments(self, settings: SimpleNamespace,
                            display_paths: bool = False) -> list[str]:
        arguments: list[str] = []
        fs = settings.file_system.get().casefold()
        arguments.append(f"-fs={fs}")
        if fs in ("hashfsv1", "hashfsv2"):
            if settings.compression.get():
                arguments.append(f"-cm={settings.compression.get()}")
            if settings.salt.get():
                arguments.append(f"--salt={settings.salt.get()}")
            if fs == "hashfsv1":
                arguments.append(f"-tm={settings.v1_table_position.get()}")
            else:
                table_compressed = settings.v2_table_compressed.get()
                metadata_compressed = settings.v2_metadata_compressed.get()
                if table_compressed and metadata_compressed:
                    compression = "c"
                elif not table_compressed and not metadata_compressed:
                    compression = "u"
                elif table_compressed:
                    compression = "um"
                else:
                    compression = "ut"
                arguments.append(
                    f"-tm={settings.v2_table_position.get()}t"
                    f"{settings.v2_metadata_position.get()}m{compression}")
            if settings.dry_run.get():
                arguments.append("--dry-run")
            if settings.encrypt_mode.get() == "all":
                arguments.append("-e=all")
            elif settings.encrypt_mode.get() == "list" and settings.encrypt_list.get():
                value = settings.encrypt_list.get()
                arguments.extend(["-e", _display_path(value) if display_paths else value])
            if settings.hide_mode.get() == "all":
                arguments.append("-h=all")
            elif settings.hide_mode.get() == "list" and settings.hide_list.get():
                value = settings.hide_list.get()
                arguments.extend(["-h", _display_path(value) if display_paths else value])
            for option, variable in (("-fh", settings.force_hide),
                                     ("-x", settings.exclude),
                                     ("-ref", settings.reference)):
                if variable.get():
                    value = variable.get()
                    arguments.extend([option, _display_path(value) if display_paths else value])
        else:
            arguments.append(f"-es={'y' if settings.entry_sort.get() else 'n'}")
            zip_junk_parts = []
            if settings.junk_incorrect_eocd.get():
                zip_junk_parts.append("EOCD=1")
            false_entries = settings.junk_false_entries.get().strip()
            if false_entries and false_entries != "0":
                zip_junk_parts.append(f"FE={false_entries}")
            if zip_junk_parts:
                arguments.append("--junk=" + "; ".join(zip_junk_parts))
        effective_directories = settings.directories.get() and not (
            fs in ("hashfsv1", "hashfsv2") and settings.hide_mode.get() != "none")
        if settings.empty_files.get() and effective_directories:
            arguments.append("-a")
        elif settings.empty_files.get():
            arguments.append("-af")
        elif effective_directories:
            arguments.append("-ad")
        if fs in ("hashfsv1", "hashfsv2"):
            arguments.append(f"-spl={'y' if settings.sort_payload.get() else 'n'}")
            junk_arguments = (
                ("DH", settings.junk_duplicate_hash),
                ("MOOB", settings.junk_metadata_out_of_bounds),
                ("IM", settings.junk_invalid_metadata),
                ("POOB", settings.junk_payload_out_of_bounds),
                ("POT", settings.junk_payload_overlaps_table),
                ("IZH", settings.junk_invalid_zlib_header),
                ("UC", settings.junk_unknown_chunk),
                ("SCR", settings.junk_suspicious_compression),
                ("RV", settings.junk_random_valid),
            )
            junk_parts = []
            for option, variable in junk_arguments:
                if option == "UC" and fs != "hashfsv2":
                    continue
                value = variable.get().strip()
                if value and value != "0":
                    junk_parts.append(f"{option}={value}")
            if fs == "hashfsv2" and settings.junk_decoy_metadata_descriptor.get():
                junk_parts.append("DMD=1")
            if junk_parts:
                arguments.append("--junk=" + "; ".join(junk_parts))
            if fs == "hashfsv1" and settings.crash_game_by_manifest.get():
                arguments.append("-cg=BM")
        return arguments

    def arguments(self, display_paths: bool = False) -> list[str]:
        arguments: list[str] = []
        if self.inputs:
            values = [_display_path(value) for value in self.inputs] if display_paths else self.inputs
            arguments.extend(["-i", *values])
        if self.outputs:
            arguments.append("-o")
            if self.configure_each_archive.get():
                for output in self.outputs:
                    arguments.append(_display_path(output) if display_paths else output)
                    arguments.append("->")
                    settings_args = self._settings_arguments(
                        self._settings_for_output(output), display_paths)
                    arguments.append(subprocess.list2cmdline(settings_args))
            else:
                values = [_display_path(value) for value in self.outputs] if display_paths else self.outputs
                arguments.extend(values)
        if not (self.outputs and self.configure_each_archive.get()):
            arguments.extend(self._settings_arguments(
                self._global_settings(), display_paths))
        arguments.append({"minimal": "--verbose=min", "progress": "--verbose=bar",
                          "detailed": "--verbose=max"}[self.verbosity.get()])
        return arguments

    def command(self) -> str:
        parts = [subprocess.list2cmdline([r".\UniversalSCSPacker.exe"])]
        for argument in self.arguments(display_paths=True):
            parts.append(subprocess.list2cmdline([argument]))
        return " ".join(parts)

    def refresh_command(self) -> None:
        if not hasattr(self, "command_preview"):
            return
        self.command_preview.set(self.command())

    def copy_command(self) -> None:
        self._set_clipboard(self.command())
        self.status.set(self.t("Команда скопирована"))
        self.root.after(1800, lambda: self.status.set(
            self.t("Выполняется…") if self.operations else self.t("Готово")))

    def copy_log(self) -> None:
        log = self.active_log()
        try:
            text = log.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            text = self._log_model_text(log)
        self._set_clipboard(text)

    def _log_model_text(self, log: tk.Text) -> str:
        rows: list[str] = []
        for record in self.log_models.get(log, ()):
            kind = record.get("kind")
            if kind == "raw":
                rows.append(str(record["text"]).rstrip("\r\n"))
            elif kind in ("line", "progress"):
                original = str(record["original"])
                visible = (_translate_output_ru(original)
                           if self.language_code == "ru" else original)
                rows.append(visible.rstrip("\r\n"))
            elif kind == "group":
                lines = record.get("lines", [])
                if isinstance(lines, list):
                    group_kind = str(record.get("group_kind") or "write")
                    for original in lines:
                        visible = (_format_detail_line(str(original), self.language_code)
                                   if group_kind == "detail" else
                                   (_translate_output_ru(str(original))
                                    if self.language_code == "ru" else str(original)))
                        rows.append(visible.rstrip("\r\n"))
            elif kind == "exit":
                code = int(record["code"])
                rows.append((f"{'✓' if code == 0 else '✕'} "
                             + (f"Process exited with code {code}"
                                if self.language_code == "en"
                                else f"Процесс завершён с кодом {code}")))
        return "\n".join(rows).strip()

    def _child_command(self, arguments: list[str]) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--gui-child", *arguments]
        return [sys.executable, str(Path(__file__).with_name("packer.py")),
                "--gui-child", *arguments]

    def run(self) -> None:
        log = self.active_log()
        if any(operation["log"] is log for operation in self.operations.values()):
            messagebox.showinfo(APP_NAME, self.t("Упаковка уже выполняется."))
            return
        if not self.inputs:
            messagebox.showerror(APP_NAME, self.t("Добавьте хотя бы один входной архив или папку."))
            return
        if len(self.outputs) > len(self.inputs):
            messagebox.showerror(APP_NAME, self.t("Выходных архивов не может быть больше, чем входов."))
            return
        self._save_app_preferences()
        arguments = self.arguments()
        self.show_page("log")
        command_text = "\n▶ " + self.command() + "\n"
        self._append_log(command_text, "debug", log)
        self.log_models[log].append(
            {"kind": "raw", "text": command_text, "tag": "debug"})
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        child_environment = os.environ.copy()
        child_environment["UNIVERSAL_SCS_GUI_LEVELS"] = "1"
        child_environment["UNIVERSAL_SCS_MAX_THREADS"] = self.max_threads.get()
        try:
            process = subprocess.Popen(
                self._child_command(arguments), cwd=Path.cwd(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags, env=child_environment)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось запустить пакер:\n{exc}")
            return
        self.operation_counter += 1
        operation_id = self.operation_counter
        self.operations[operation_id] = {
            "process": process, "log": log, "write_lines": [],
            "group_kind": None, "group_count": 0}
        self.status.set(self.t("Выполняется…"))
        self._update_process_buttons()
        threading.Thread(target=self._read_process,
                         args=(operation_id, process), daemon=True).start()

    def run_in_new_tab(self) -> None:
        self.new_activity_tab()
        self.run()

    def _read_process(self, operation_id: int,
                      process: subprocess.Popen[str]) -> None:
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                self.output_queue.put((operation_id, "line", line))
            code = process.wait()
            self.output_queue.put((operation_id, "done", code))
        except (OSError, ValueError) as exc:
            self.output_queue.put((operation_id, "line", f"Error: {exc}\n"))
            self.output_queue.put((operation_id, "done", 1))

    def _line_tag(self, line: str) -> str:
        lowered = line.strip().casefold()
        if lowered.startswith("success:"):
            return "success"
        if lowered.startswith(("error:", "ошибка:")):
            return "error"
        if (lowered.startswith("traceback (most recent call last):")
                or lowered in ("memoryerror", "memory error")
                or lowered.startswith(("memoryerror:", "exception:"))
                or (lowered.startswith("file \"") and ", line " in lowered)):
            return "error"
        if lowered.startswith(("warning:", "warn:")):
            return "warning"
        if lowered.startswith("write "):
            return "success"
        if lowered.startswith("detail "):
            return "info"
        if lowered.startswith(("gdeflate fallback", "inputs:", "outputs:",
                               "options:", "selectors:", "  inputs:", "  ")):
            return "debug"
        return "info"

    def _flush_write_group(self, operation_id: int,
                           operation: dict[str, object], log: tk.Text) -> None:
        lines = operation.get("write_lines")
        if not isinstance(lines, list) or not lines:
            return
        at_end = bool(operation.pop(
            "follow_output", log.yview()[1] >= 0.999))
        operation["write_lines"] = []
        group_number = int(operation.get("group_count", 0)) + 1
        operation["group_count"] = group_number
        record: dict[str, object] = {
            "kind": "group", "lines": list(lines),
            "group_kind": str(operation.get("group_kind") or "write"),
            "output_name": str(operation.get("output_name", "")).strip(),
            "open": False,
            "header_tag": f"write_header_{operation_id}_{group_number}",
            "content_tag": f"write_content_{operation_id}_{group_number}",
        }
        self.log_models[log].append(record)
        self._insert_write_group(log, record)
        if at_end:
            log.see(tk.END)
        operation["group_kind"] = None

    def _insert_write_group(self, log: tk.Text,
                            record: dict[str, object]) -> None:
        lines = record["lines"]
        assert isinstance(lines, list)
        header_tag = str(record["header_tag"])
        content_tag_name = str(record["content_tag"])
        group_kind = str(record.get("group_kind") or "write")
        style_tag = "info" if group_kind == "detail" else "success"
        if group_kind == "detail":
            first = str(lines[0]) if lines else ""
            raw_detail = first.strip()
            if raw_detail.startswith("DETAIL "):
                raw_detail = raw_detail[len("DETAIL "):]
            detail_label = raw_detail.split(" | ", 1)[0]
            if self.language_code == "ru":
                detail_label = {
                    "Preparing HashFS entries": "Подготовка записей HashFS",
                    "Preparing HashFS v2 entries": "Подготовка записей HashFS v2",
                    "Converting DDS to DX10": "Преобразование DDS в DX10",
                    "Encoding GDeflate textures": "Кодирование текстур GDeflate",
                    "Compressing ZIP entries": "Сжатие записей ZIP",
                }.get(detail_label, detail_label)
            title = (f"{detail_label} ({len(lines)})"
                     if self.language_code == "en"
                     else f"{detail_label} ({len(lines)})")
        else:
            directory_count = sum(
                "[directory;" in str(line).casefold() for line in lines)
            file_count = len(lines) - directory_count
            if self.language_code == "en":
                title = (f"Archive entries written: {len(lines)} "
                         f"({file_count} files, {directory_count} directories)")
            else:
                title = (f"Записано элементов архива: {len(lines)} "
                         f"({file_count} файлов, {directory_count} каталогов)")
        output_name = str(record.get("output_name", "")).strip()
        if output_name:
            title = f"{output_name}: {title[:1].lower()}{title[1:]}"
        marker = "▼" if record.get("open") else "▶"
        log.insert(tk.END, f"{marker} {title}\n", (header_tag, style_tag))

        def insert_lines(index: str = tk.END) -> None:
            rendered: list[str] = []
            for line in lines:
                original = str(line)
                visible = (_format_detail_line(original, self.language_code)
                           if group_kind == "detail" else
                           (_translate_output_ru(original)
                            if self.language_code == "ru" else original))
                rendered.append(visible if visible.endswith("\n") else visible + "\n")
            # Insert the whole block once. Reusing one fixed Text index for
            # individual rows reverses their order when a group is expanded.
            log.insert(index, "".join(rendered), (content_tag_name, style_tag))

        # Collapsed groups keep their lines in the Python model only.  Keeping
        # thousands of elided Text rows made Tk recalculate the whole scroll
        # range on every movement and caused repeatable pauses while scrolling.
        if record.get("open"):
            insert_lines()

        def toggle(_event=None) -> str:
            opening = not bool(record.get("open"))
            record["open"] = opening
            ranges = log.tag_ranges(header_tag)
            if ranges:
                if opening:
                    insert_lines(str(ranges[-1]))
                else:
                    content_ranges = log.tag_ranges(content_tag_name)
                    if content_ranges:
                        log.delete(content_ranges[0], content_ranges[-1])
                    ranges = log.tag_ranges(header_tag)
                    if not ranges:
                        return "break"
                start = ranges[0]
                log.delete(start, f"{start}+1c")
                log.insert(start, "▼" if opening else "▶",
                           (header_tag, style_tag))
            return "break"

        log.tag_bind(header_tag, "<Button-1>", toggle)
        log.tag_configure(header_tag, underline=True)

    def _update_progress_line(self, operation: dict[str, object],
                              log: tk.Text, line: str) -> None:
        # Text indices are not stable after an insertion: a saved string such
        # as ``12.0`` can end up pointing at an earlier line when Tk appends
        # more output.  A unique tag follows the actual progress text and lets
        # us replace precisely that range on every update.
        at_end = log.yview()[1] >= 0.999
        position = log.yview()[0]
        operation["follow_output"] = at_end
        progress_tag = operation.get("progress_tag")
        if not isinstance(progress_tag, str):
            serial = int(operation.get("progress_serial", 0)) + 1
            operation["progress_serial"] = serial
            progress_tag = f"progress_{id(operation)}_{serial}"
            operation["progress_tag"] = progress_tag
            record = {"kind": "progress", "original": line, "tag": "info",
                      "progress_tag": progress_tag}
            operation["progress_record"] = record
            self.log_models[log].append(record)
        else:
            record = operation.get("progress_record")
            if isinstance(record, dict):
                record["original"] = line
        ranges = log.tag_ranges(progress_tag)
        if ranges:
            log.delete(ranges[0], ranges[-1])
        visible = _translate_output_ru(line) if self.language_code == "ru" else line
        log.insert(tk.END, visible.rstrip("\r\n") + "\n", ("info", progress_tag))
        if at_end:
            log.see(tk.END)
        else:
            log.yview_moveto(position)

    @staticmethod
    def _progress_text(label: str, current: int, total: int) -> str:
        total = max(1, total)
        fraction = min(1.0, current / total)
        width = 28
        filled = int(width * fraction)
        bar = "#" * filled + "-" * (width - filled)
        return f"{label}: [{bar}] {current}/{total} ({fraction:.0%})\n"

    @staticmethod
    def _finish_progress_line(operation: dict[str, object]) -> None:
        operation.pop("progress_tag", None)
        operation.pop("progress_record", None)

    def _append_output_record(self, log: tk.Text, original: str,
                              tag: str | None) -> None:
        record = {"kind": "line", "original": original,
                  "tag": tag or self._line_tag(original)}
        self.log_models[log].append(record)
        visible = (_translate_output_ru(original)
                   if self.language_code == "ru" else original)
        self._append_log(visible, str(record["tag"]), log)

    def _insert_exit_record(self, log: tk.Text, record: dict[str, object]) -> None:
        code = int(record["code"])
        text = (f"\n{'✓' if code == 0 else '✕'} "
                + ((f"Process exited with code {code}\n")
                   if self.language_code == "en"
                   else f"Процесс завершён с кодом {code}\n"))
        self._append_log(text, "success" if code == 0 else "error", log)

    def _rerender_all_logs(self) -> None:
        for log in self.activity_logs:
            at_end = log.yview()[1] >= 0.999
            position = log.yview()[0]
            log.delete("1.0", tk.END)
            for record in self.log_models.get(log, ()):
                kind = record.get("kind")
                if kind == "raw":
                    log.insert(tk.END, str(record["text"]), str(record["tag"]))
                elif kind in ("line", "progress"):
                    original = str(record["original"])
                    visible = (_translate_output_ru(original)
                               if self.language_code == "ru" else original)
                    tags = [str(record["tag"])]
                    if kind == "progress":
                        tags.append(str(record["progress_tag"]))
                    log.insert(tk.END, visible, tuple(tags))
                elif kind == "group":
                    self._insert_write_group(log, record)
                elif kind == "exit":
                    self._insert_exit_record(log, record)
            if at_end:
                log.see(tk.END)
            else:
                log.yview_moveto(position)

    def _append_log(self, text: str, tag: str | None = None,
                    log: tk.Text | None = None) -> None:
        log = log or self.active_log()
        at_end = log.yview()[1] >= 0.999
        log.insert(tk.END, text, tag or self._line_tag(text))
        if at_end:
            log.see(tk.END)

    def _drain_output(self) -> None:
        for _ in range(500):
            try:
                operation_id, kind, value = self.output_queue.get_nowait()
            except queue.Empty:
                break
            operation = self.operations.get(operation_id)
            if operation is None:
                continue
            log = operation["log"]
            assert isinstance(log, tk.Text)
            if kind == "line":
                line = str(value)
                if line.startswith(GUI_PROGRESS_DETAIL_PREFIX):
                    payload = line[len(GUI_PROGRESS_DETAIL_PREFIX):].rstrip("\r\n")
                    parts = payload.split("\x1f", 3)
                    if len(parts) == 4:
                        label, current_text, total_text, detail_line = parts
                        try:
                            current = int(current_text)
                            total = int(total_text)
                        except ValueError:
                            current = total = 0
                        previous_label = operation.get("combined_progress_label")
                        if previous_label not in (None, label):
                            self._finish_progress_line(operation)
                            self._flush_write_group(operation_id, operation, log)
                        operation["combined_progress_label"] = label
                        self._update_progress_line(
                            operation, log,
                            self._progress_text(label, current, total))
                        group_kind = ("write" if detail_line.lstrip().lower().startswith("write ")
                                      else "detail")
                        pending_kind = operation.get("group_kind")
                        pending_lines = operation.get("write_lines")
                        if (isinstance(pending_lines, list) and pending_lines
                                and pending_kind != group_kind):
                            self._flush_write_group(operation_id, operation, log)
                        operation["group_kind"] = group_kind
                        write_lines = operation.get("write_lines")
                        assert isinstance(write_lines, list)
                        write_lines.append(detail_line + "\n")
                        continue
                explicit_tag = None
                level_match = LEVEL_MARKER.match(line)
                if level_match is not None:
                    explicit_tag = level_match.group(1).casefold()
                    line = line[level_match.end():]
                original_line = line
                if original_line.strip().casefold().startswith(
                        "traceback (most recent call last):"):
                    operation["traceback_active"] = True
                if explicit_tag is None and operation.get("traceback_active"):
                    # An unhandled Python exception is fatal for the child.
                    # Keep every frame, source excerpt and terminal exception
                    # in one red block instead of classifying frames as INFO.
                    explicit_tag = "error"
                if PROGRESS_LINE.search(original_line.strip()):
                    self._update_progress_line(operation, log, original_line)
                elif not original_line.strip() and "progress_tag" in operation:
                    self._finish_progress_line(operation)
                elif (original_line.lstrip().lower().startswith("write ")
                      or original_line.lstrip().lower().startswith("detail ")):
                    self._finish_progress_line(operation)
                    group_kind = ("write" if original_line.lstrip().lower().startswith("write ")
                                  else "detail")
                    pending_kind = operation.get("group_kind")
                    pending_lines = operation.get("write_lines")
                    if (isinstance(pending_lines, list) and pending_lines
                            and pending_kind != group_kind):
                        self._flush_write_group(operation_id, operation, log)
                    operation["group_kind"] = group_kind
                    write_lines = operation.get("write_lines")
                    assert isinstance(write_lines, list)
                    write_lines.append(original_line)
                else:
                    self._finish_progress_line(operation)
                    self._flush_write_group(operation_id, operation, log)
                    operation.pop("combined_progress_label", None)
                    stripped = original_line.strip()
                    if stripped.startswith("Output ") and ":" in stripped:
                        output_path = stripped.split(":", 1)[1].strip()
                        operation["output_name"] = Path(output_path).name
                    self._append_output_record(log, original_line, explicit_tag)
            else:
                code = int(value)
                self._finish_progress_line(operation)
                self._flush_write_group(operation_id, operation, log)
                operation.pop("combined_progress_label", None)
                self.operations.pop(operation_id, None)
                self.status.set(self.t("Завершено успешно") if code == 0
                                else ((f"Exited with code {code}") if self.language_code == "en"
                                      else f"Завершено с кодом {code}"))
                exit_record = {"kind": "exit", "code": code}
                self.log_models[log].append(exit_record)
                self._insert_exit_record(log, exit_record)
                self._update_process_buttons()
        self.root.after(50, self._drain_output)

    def stop(self) -> None:
        log = self.active_log()
        for operation in self.operations.values():
            if operation["log"] is not log:
                continue
            process = operation["process"]
            if isinstance(process, subprocess.Popen) and process.poll() is None:
                self.status.set(self.t("Остановка…"))
                try:
                    process.terminate()
                except OSError:
                    pass
            break

    def _update_process_buttons(self) -> None:
        if not hasattr(self, "stop_button"):
            return
        self._refresh_activity_tabs()
        log = self.active_log() if hasattr(self, "activity_notebook") else None
        active_running = any(operation["log"] is log
                             for operation in self.operations.values())
        self.stop_button.configure(state=tk.NORMAL if active_running else tk.DISABLED)

    def close(self) -> None:
        if self.operations:
            if not messagebox.askyesno(APP_NAME, self.t("Упаковка выполняется. Остановить её и выйти?")):
                return
            for operation in tuple(self.operations.values()):
                process = operation["process"]
                if isinstance(process, subprocess.Popen) and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
        self._save_app_preferences()
        self.root.destroy()


def run_gui() -> int:
    _hide_standalone_console()
    _set_windows_app_identity()
    root = tk.Tk()
    gui = PackerGui(root)
    _apply_windows_window_icon(root, gui._asset("app-icon.ico"))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui())
