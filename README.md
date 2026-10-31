# Universal SCS Packer

[Русский](#русская-версия) · [English](#english-version)

<a id="русская-версия"></a>

## Русская версия

Universal SCS Packer — графический и консольный упаковщик модов Euro Truck Simulator 2 и American Truck Simulator. Он создаёт HashFS v1, HashFS v2, Ghostly Encrypted ZIP и AEM, принимает сразу несколько папок или архивов, умеет распределять источники между несколькими выходными файлами и проверяет, можно ли безопасно скрыть путь от каталогов игры.

Приложение написано на Python. GUI выполнен в том же стиле, что Universal SCS Extractor, поддерживает русский и английский языки, вкладки параллельных операций, копирование журнала, цветовые уровни и сворачиваемые подробности. При запуске без параметров открывается интерфейс, а при передаче параметров работает CLI.

## Поддерживаемые выходные форматы

### HashFS v1

Классическая файловая система SCS с одной hash-sorted таблицей записей, CityHash64 и обычными zlib-потоками. По умолчанию salt равен `0`; ненулевая соль поддерживается, а рядом автоматически создаётся ZIP-заглушка `stub_salt0.scs` с пустым каталогом `/base/def` для совместимости игры. Можно выбирать положение таблицы, скрывать пути из directory listings, шифровать SII через 3nK, переставлять payload и сохранять пустые каталоги.

### HashFS v2

Формат с отдельной таблицей путей и таблицей метаданных, 16-байтовым выравниванием payload, typed metadata и ненулевой случайной солью по умолчанию. Положение и сжатие обеих таблиц настраиваются независимо. Валидные бинарные пары TOBJ+DDS преобразуются в DX10 и объединяются в обязательную GDeflate-запись через Microsoft DirectStorage. Если TOBJ ссылается на DDS из другого входа или `-ref`, зависимость переносится в тот же выходной архив.

### Ghostly eZIP

ZIP с обычным Deflate-содержимым, но с установленным флагом шифрования в local и central headers. Поддерживаются нестандартный ложный EOCD и ложные записи центрального каталога. Формат предназначен для совместимого с игрой обфусцированного ZIP, а не для криптографической защиты.

### AEM

Архив с `AEM!` local records без обычной локальной ZIP-метадаты и с обфусцированным ZIP-подобным центральным каталогом. Поддерживает те же входы, порядок записей, пустые файлы/каталоги и ZIP-развлечения, что eZIP.

## Возможности

- Вход из папок, ZIP, 7z, RAR, TAR и других форматов, которые распознаёт встроенный 7-Zip backend; расширение файла не обязательно должно совпадать с форматом.
- Объединение нескольких источников по порядку: более поздний источник заменяет совпадающий путь из раннего.
- Создание нескольких выходных архивов за один запуск с последовательным распределением входов.
- Индивидуальные настройки каждого выходного архива через GUI или синтаксис `output.scs -> "options"`.
- Автоматическая параллельная проверка безопасности, чтение, 3nK, zlib, конвертация DDS, GDeflate и подготовка HashFS-записей.
- Максимальное число потоков настраивается глобально и сохраняется рядом с приложением.
- Четыре уровня zlib: max, medium, min и без обычного сжатия.
- Скрытие файлов и каталогов по рекурсивным спискам или `all` с защитой чувствительных SII, locale, unit и секторов карты; все SUI скрываются без дополнительных условий.
- Принудительное скрытие отдельным списком с явным предупреждением о риске невидимости файлов для игры.
- 3nK-шифрование выбранных или всех `.sii`.
- Исключение файлов и каталогов, справочные архивы/папки, сохранение пустых файлов и пустых каталогов.
- Перемешивание физического порядка payload при сохранении обязательной сортировки HashFS-таблицы по хешам.
- Dry run без создания выходного файла.
- Три режима подробности: только этапы, этапы с progress bar, полный список записей.
- Атомарная запись: архив сначала создаётся во временном файле в папке назначения и заменяет итоговый только после успешного завершения.
- Диагностические «Развлечения» для генерации различных junk-записей HashFS и ложных ZIP/AEM-структур.

## GUI

На главной странице выбираются входные источники, один или несколько выходов, глобальная подробность и режим индивидуальной настройки архивов. Новый `.scs` можно создать через кнопку `+`, сразу указав имя и папку. Если включено «Настроить каждый архив», страница параметров показывает отдельный сворачиваемый блок каждого выхода; при отключении сохраняются настройки последнего архива.

Страница параметров динамически показывает только опции выбранного формата. Для HashFS v2 положение и сжатие таблицы/метаданных выбираются раздельно, для HashFS v1 — положение единственной таблицы. Случайный режим не перестраивает страницу. Колесо мыши прокручивает страницу, но не изменяет закрытые select-поля.

Журнал разделяет DEBUG, INFO, SUCCESS, WARNING и ERROR цветом, но не добавляет эти слова перед каждой строкой. При максимальной подробности progress bar превращается в сворачиваемый список завершённого этапа. Каждая операция живёт в собственной вкладке.

## Быстрый старт

```powershell
# Упаковать папку в HashFS v2 с настройками по умолчанию
.\UniversalSCSPacker.exe -i .\my_mod -o .\my_mod.scs

# Объединить несколько источников
.\UniversalSCSPacker.exe -i .\base.zip .\patch.7z .\local -o .\combined.scs -h=all -e=all -v

# HashFS v1
.\UniversalSCSPacker.exe -i .\mod -o .\mod_v1.scs -fs=v1 --salt=0 -cm=max

# Два разных формата за один запуск
.\UniversalSCSPacker.exe -i .\part1 .\part2 -o .\first.scs '->' '-fs=v2 -h=all' .\second.scs '->' '-fs=aem -a' --verbose=max

# Проверить упаковку без создания файла
.\UniversalSCSPacker.exe -i .\mod -o .\mod.scs --dry-run
```

Запуск из исходников:

```powershell
python .\packer.py -i .\my_mod -o .\my_mod.scs
```

## Входы и выходы

| Параметр | Назначение |
| --- | --- |
| `-i SOURCE...` | Один или несколько каталогов/архивов. После `-i` перечисляется весь массив входов. |
| `-o FILE...` | Один или несколько выходных `.scs`. Если параметр не задан, имя строится по первому источнику. |
| `FILE -> "OPTIONS"` | Индивидуальные параметры конкретного выхода. Подробность остаётся общей и задаётся после всех выходов. |

Если выход один, в него объединяются все входы. Если выходов несколько, входы распределяются последовательными группами с резервированием хотя бы одного входа каждому оставшемуся выходу: 7 входов и 3 выхода дают группы 3/3/1. DDS-зависимость всегда переносится к архиву, содержащему соответствующий TOBJ.

## Общие параметры архивов

| Параметр | Назначение |
| --- | --- |
| `--file-system TYPE`, `-fs=v2|v1|ez|aem` | `hashfsv2` (по умолчанию), `hashfsv1`, `ezip` или `aem`. |
| `-af` | Сохранять пустые файлы. |
| `-ad` | Сохранять пустые каталоги. В HashFS при `-h` отключается, чтобы не раскрывать скрытые пути. |
| `-a` | Одновременно `-af -ad`. |
| `--dry-run` | Полностью прочитать и подготовить данные, но не создавать/заменять выход. |
| `--verbose=min|bar|max` | Минимальный вывод, progress bar или максимальная детализация; пустой `--verbose` означает `max`. |
| `-v0` | Только этапы и итог. |
| `-v1` | Этапы и progress bar; режим по умолчанию. |
| `-v2`, `-v` | Максимальная детализация по записям. |

## HashFS: сжатие, salt и размещение таблиц

| Параметр | Назначение |
| --- | --- |
| `--salt=VALUE`, `-salt=VALUE` | Число `0..65535`, hex или `random`. По умолчанию v1 использует 0, v2 — случайное ненулевое значение. |
| `--compression-method METHOD`, `-cm METHOD` | Уровень для всех HashFS payload: `max`, `medium`, `min` или `no`. Для обычных записей это zlib 9/6/1/raw, для текстур v2 — GDeflate BEST_RATIO/DEFAULT/FASTEST либо raw-поверхность. |
| `--sort-payload=enabled|disabled`, `-spl=y|n` | Перемешать физическое размещение payload. По умолчанию включено. |
| `--table-mode MODE`, `-tm=MODE` | Положение таблиц и, для v2, способ их сжатия. |

HashFS v1 принимает режимы: `1` — offset `0x1000`, `2` — конец архива, `3` — сразу после header (`0x20`), `4` — после случайного payload.

HashFS v2 использует форму `<N>t_<N>m_<compression>`: `t` — path table, `m` — metadata. Позиции: `1` = `0x1000`, `2` = конец, `3` = сразу после header (`0x40`), `4` = после случайного payload. Компоненты можно переставлять и писать без подчёркиваний. Суффиксы:

- `c` / `compressed` — обе таблицы сжаты;
- `u` / `uncompressed` — обе не сжаты;
- `um` / `uncompressed_metadata` — metadata не сжата;
- `ut` / `uncompressed_table` — path table не сжата;
- `cm` / `compressed_metadata` — metadata сжата, path table нет;
- `ct` / `compressed_table` — path table сжата, metadata нет;
- `r` / `random` — случайная комбинация.

Режим по умолчанию — `2t_2m_compressed`: обе таблицы сжаты и находятся в конце, payload начинается сразу после header. При пересечении областей пакер автоматически перемещает конфликтующую таблицу или metadata и сообщает новый offset.

## Списки путей и безопасность скрытия

Все списки читаются как UTF-8 или UTF-16 с BOM, по одному относительному пути в строке. Начальный `/` необязателен. Каталог является рекурсивным селектором: `automat/` выбирает всё под `/automat`.

| Параметр | Назначение |
| --- | --- |
| `-e LIST.txt`, `-e=all` | Зашифровать выбранные или все `.sii` оболочкой 3nK. |
| `-h LIST.txt`, `-h=all` | Скрыть выбранные записи из directory listings с обязательной проверкой безопасности. |
| `-fh LIST.txt`, `--force-hide LIST.txt` | Принудительно скрыть точный список, обходя проверки. Может сделать файлы невидимыми игре. |
| `-x LIST.txt` | Полностью исключить выбранные файлы/каталоги. |
| `-ref FOLDER|ARCHIVE` | Добавить справочный источник для безопасного скрытия и поиска недостающих DDS. Можно повторять. |

Для секторов карты, `def/*.sii`, `unit/*.sii` и `/locale` путь скрывается только когда он присутствует в `original_game_paths.txt`, найден в `-ref` или явно упомянут во входном текстовом файле. **Все файлы `.sui` разрешено скрывать независимо от их каталога.** Проверенные дочерние семейства sign/cargo/vehicle accessory также разрешаются автоматически. Модели, материалы и текстуры обычно находятся игрой по прямым ссылкам и не требуют directory listing.

Пакер сохраняет листинг `/def`, а выбранные через `-h` листинги `/def/city` и `/def/company` удаляет как безопасно избыточные. Файловые payload этих каталогов остаются в архиве и доступны по своим хешам.

Готовый `original_game_paths.tar.gz` находится в корне проекта, участвует в сборке и не пересобирается автоматически при каждом build.

## GDeflate и DDS в HashFS v2

Тип GDeflate-текстуры обязателен и отдельного переключателя не имеет. Пакер анализирует бинарный TOBJ, находит DDS, при необходимости преобразует поддерживаемый legacy DDS в DX10 и кодирует пару единой записью. `-cm=max`, `medium` и `min` выбирают соответственно BEST_RATIO, DEFAULT и FASTEST в DirectStorage; `-cm=no` сохраняет поверхность raw внутри той же texture record. Если один DDS используется несколькими TOBJ, каждая логическая пара учитывается отдельно — это корректно для формата.

Если DDS отсутствует во входах, пакер ищет его в `-ref`. Найденная зависимость добавляется в соответствующий выход. Если ссылка так и не разрешена, TOBJ сохраняется отдельно без GDeflate, все отсутствующие пути перечисляются, а общая рекомендация указать распакованные игровые архивы показывается только когда `-ref` вообще не задан.

## eZIP и AEM

| Параметр | Назначение |
| --- | --- |
| `--entry-sort=enabled|disabled`, `-es=y|n` | Сортировать каталоги/файлы или случайно перемешать их. По умолчанию выключено. |
| `-a`, `-af`, `-ad` | Сохранять пустые файлы и явные ZIP-каталоги. |
| `--junk="EOCD=1; FE=N"` | Создать неправильный EOCD и/или до 10000 ложных central-directory entries. |

При выключенной сортировке явный каталог всё равно располагается раньше собственных потомков, чтобы архивные программы корректно видели его флаг. FE распределяет примерно треть ложных записей в начале, остальные — между полными нормальными local entries.

## Развлечения и диагностические записи

`--junk="NAME=N; ..."` создаёт выбранные типы junk. Короткие имена:

| Имя | Полное название | Формат/ограничение |
| --- | --- | --- |
| `DH` | `DuplicateHash` | HashFS; в v2 до 2, в v1 до 50000. |
| `MOOB` | `MetadataOutOfBounds` | HashFS, до 30. |
| `IM` | `InvalidMetadata` | HashFS, до 30. |
| `POOB` | `PayloadOutOfBounds` | HashFS, до 30. |
| `POT` | `PayloadOverlapsTable` | HashFS, до 30. |
| `IZH` | `InvalidZlibHeader` | HashFS, до 30. |
| `UC` | `UnknownChunk` | Только HashFS v2, до 100. |
| `SCR` | `SuspiciousCompressionRatio` | HashFS, до 50000. |
| `RV` | `RandomValid` | До 1000 корректно оформленных случайных записей с игровыми расширениями и размером до 15 MiB. |
| `DMD` | `DecoyMetadataDescriptor` | Только HashFS v2, 0 или 1: ложные descriptor-блоки перед настоящими. |
| `EOCD` | `IncorrectEOCD` | eZIP/AEM, 0 или 1. |
| `FE` | `FalseEntries` | eZIP/AEM, до 10000. |

`--crash-game=ByManifest` / `-cg=BM` доступен только HashFS v1 и намеренно завышает declared sizes `manifest.sii`. Игра с подключённым таким модом не запустится; функция отделена предупреждением в GUI и предназначена исключительно для контролируемых экспериментов.

## Сборка

Требуется Python 3.11+; сборочные зависимости находятся в `requirements-build.txt`.

```powershell
python .\build.py                    # формат текущей ОС
python .\build.py --format exe       # Windows
python .\build.py --format deb       # Debian/Ubuntu
python .\build.py --format rpm       # RPM-based Linux
python .\build.py --format app       # macOS
```

`--skip-install` не позволяет скрипту устанавливать отсутствующие build-зависимости. Windows-обёртка `build.ps1` создаёт `dist/UniversalSCSPacker.exe`. Пакеты всех платформ помещаются непосредственно в `dist`.

Для HashFS v2 в Windows рядом с исходниками должны находиться необходимые библиотеки DirectStorage. Форматы 7z/RAR обслуживаются содержимым `ArchiveBackend`. Их собственные лицензии и notices сохранены в соответствующих каталогах.

## Лицензия

Основной проект распространяется на условиях **GNU General Public License v2.0** — см. [LICENSE](LICENSE). Сторонние компоненты сохраняют собственные условия в `DirectStorage` и `ArchiveBackend`.

Проект не связан с SCS Software. Названия Euro Truck Simulator, American Truck Simulator и SCS используются только для описания совместимости.

---

<a id="english-version"></a>

## English version

Universal SCS Packer is a graphical and command-line packer for Euro Truck Simulator 2 and American Truck Simulator mods. It creates HashFS v1, HashFS v2, Ghostly Encrypted ZIP, and AEM archives, accepts several folders or archives at once, distributes inputs between multiple outputs, and verifies whether a path can be safely removed from the game's directory listings.

The application is written in Python. Its GUI follows the same design as Universal SCS Extractor and supports English and Russian, parallel-operation tabs, journal text copying, colored message levels, and collapsible details. Starting it without arguments opens the GUI; supplying command-line arguments starts the CLI.

### Supported output formats

#### HashFS v1

The classic SCS filesystem with one hash-sorted entry table, CityHash64, and ordinary zlib streams. Salt defaults to `0`. A non-zero salt is supported and automatically creates a companion ZIP archive named `stub_salt0.scs`, containing the empty `/base/def` directory for game compatibility. The table position, path visibility, 3nK encryption, payload order, and empty-directory storage are configurable.

#### HashFS v2

A format with separate path and metadata tables, 16-byte payload alignment, typed metadata, and a random non-zero salt by default. Placement and compression can be configured independently for both tables. Valid binary TOBJ+DDS pairs are converted to DX10 and combined into mandatory GDeflate texture entries through Microsoft DirectStorage. If a TOBJ references a DDS from another input or `-ref`, that dependency is copied into the same output archive.

#### Ghostly eZIP

A ZIP archive containing ordinary Deflate payloads while the encryption flag is set in local and central headers. It supports a misleading EOCD and false central-directory records. This is game-compatible ZIP obfuscation, not cryptographic protection.

#### AEM

An archive using `AEM!` local records without normal local ZIP metadata and an obfuscated ZIP-like central directory. It supports the same input handling, entry ordering, empty files/directories, and ZIP entertainment options as eZIP.

### Features

- Input from folders, ZIP, 7z, RAR, TAR, and other formats recognized by the bundled 7-Zip backend; the filename extension does not have to match the actual format.
- Ordered source overlays: a later source replaces the same path from an earlier source.
- Multiple output archives in one operation with consecutive input distribution.
- Individual options for every output through the GUI or `output.scs -> "options"` syntax.
- Automatic parallel hide-safety checks, reading, 3nK processing, zlib compression, DDS conversion, GDeflate encoding, and HashFS entry preparation.
- A global configurable maximum worker count stored beside the application.
- Four ordinary compression modes: max, medium, min, and no compression.
- Recursive path-list or `all` hiding with safeguards for sensitive SII definitions, locale resources, unit files, and map sectors; every SUI may be hidden without additional conditions.
- Force-hide lists with an explicit warning that the files may become invisible to the game.
- 3nK encryption of selected or all `.sii` files.
- Exclusion lists, reference folders/archives, empty-file storage, and empty-directory storage.
- Randomized physical payload order while preserving the mandatory hash order of HashFS tables.
- Dry runs without creating a destination archive.
- Minimal, progress-bar, and full per-entry verbosity modes.
- Atomic output: the archive is first written to a temporary file in the destination directory and replaces the final path only after successful completion.
- Diagnostic entertainment options for generating multiple HashFS junk classes and misleading ZIP/AEM structures.

### Graphical interface

The main page selects input sources, one or more destinations, global verbosity, and per-output configuration. The `+` button creates a new `.scs` destination by choosing its name and directory. When “Configure every archive” is enabled, the Options page displays a separate collapsible block for every output. Disabling it retains the settings of the last archive.

The Options page shows only controls supported by the selected format. HashFS v2 exposes table and metadata placement/compression separately, while HashFS v1 exposes the position of its single table. Randomization does not rebuild the complete page. The mouse wheel scrolls the page without changing closed selection fields.

The journal distinguishes DEBUG, INFO, SUCCESS, WARNING, and ERROR by color without prefixing every line with the level name. At maximum verbosity, a progress bar becomes a collapsible completed-operation list. Every operation has its own journal tab.

### Quick start

```powershell
# Pack a folder as HashFS v2 with default settings
.\UniversalSCSPacker.exe -i .\my_mod -o .\my_mod.scs

# Merge several sources
.\UniversalSCSPacker.exe -i .\base.zip .\patch.7z .\local -o .\combined.scs -h=all -e=all -v

# Create HashFS v1
.\UniversalSCSPacker.exe -i .\mod -o .\mod_v1.scs -fs=v1 --salt=0 -cm=max

# Create two different formats in one operation
.\UniversalSCSPacker.exe -i .\part1 .\part2 -o .\first.scs '->' '-fs=v2 -h=all' .\second.scs '->' '-fs=aem -a' --verbose=max

# Validate and prepare without creating an archive
.\UniversalSCSPacker.exe -i .\mod -o .\mod.scs --dry-run
```

Run directly from the sources:

```powershell
python .\packer.py -i .\my_mod -o .\my_mod.scs
```

### Inputs and outputs

| Option | Description |
| --- | --- |
| `-i SOURCE...` | One or more input folders or archives. The complete input array follows one `-i`. |
| `-o FILE...` | One or more destination `.scs` files. If omitted, the name is derived from the first input. |
| `FILE -> "OPTIONS"` | Per-output option string. Verbosity remains global and follows all output definitions. |

With one output, every input is merged into it. With several outputs, inputs are assigned in consecutive groups while reserving at least one input for every remaining output: seven inputs and three outputs become groups of 3/3/1. A DDS dependency is always moved to the archive containing its referencing TOBJ.

### Common archive options

| Option | Description |
| --- | --- |
| `--file-system TYPE`, `-fs=v2|v1|ez|aem` | Select `hashfsv2` (default), `hashfsv1`, `ezip`, or `aem`. |
| `-af` | Preserve zero-byte files. |
| `-ad` | Preserve empty directories. For HashFS, `-h` disables this option so hidden paths are not exposed. |
| `-a` | Equivalent to `-af -ad`. |
| `--dry-run` | Read and prepare everything without creating or replacing the destination. |
| `--verbose=min|bar|max` | Phase-only, progress-bar, or maximum detail. Bare `--verbose` means `max`. |
| `-v0` | Phases and final summary only. |
| `-v1` | Phases plus progress bars; the default mode. |
| `-v2`, `-v` | Maximum per-entry detail. |

### HashFS compression, salt, and table placement

| Option | Description |
| --- | --- |
| `--salt=VALUE`, `-salt=VALUE` | Decimal `0..65535`, hexadecimal, or `random`. V1 defaults to 0; v2 defaults to a random non-zero value. |
| `--compression-method METHOD`, `-cm METHOD` | Level for every HashFS payload: `max`, `medium`, `min`, or `no`. Ordinary entries use zlib 9/6/1/raw; v2 textures use GDeflate BEST_RATIO/DEFAULT/FASTEST or a raw surface. |
| `--sort-payload=enabled|disabled`, `-spl=y|n` | Randomize physical payload placement. Enabled by default. |
| `--table-mode MODE`, `-tm=MODE` | Configure table placement and, for v2, table compression. |

HashFS v1 modes are: `1` — offset `0x1000`, `2` — end of archive, `3` — immediately after the header (`0x20`), and `4` — after a randomly selected payload.

HashFS v2 uses `<N>t_<N>m_<compression>`, where `t` is the path table and `m` is metadata. Positions are: `1` = `0x1000`, `2` = archive end, `3` = immediately after the header (`0x40`), and `4` = after a random payload. Components may be reordered and underscores are optional. Compression suffixes are:

- `c` / `compressed` — both tables compressed;
- `u` / `uncompressed` — both uncompressed;
- `um` / `uncompressed_metadata` — metadata uncompressed;
- `ut` / `uncompressed_table` — path table uncompressed;
- `cm` / `compressed_metadata` — metadata compressed and path table uncompressed;
- `ct` / `compressed_table` — path table compressed and metadata uncompressed;
- `r` / `random` — random supported combination.

The default is `2t_2m_compressed`: both tables are compressed at the end, while payload begins immediately after the header. If selected regions overlap, the packer automatically relocates the conflicting table or metadata and reports the new offset.

### Path lists and hide safety

Every list is read as UTF-8 or BOM-marked UTF-16, one archive-relative path per line. A leading `/` is optional. Directory selectors are recursive: `automat/` selects everything below `/automat`.

| Option | Description |
| --- | --- |
| `-e LIST.txt`, `-e=all` | Wrap selected or all `.sii` files in 3nK encryption. |
| `-h LIST.txt`, `-h=all` | Hide selected entries from directory listings after mandatory safety checks. |
| `-fh LIST.txt`, `--force-hide LIST.txt` | Force-hide an exact list and bypass every safeguard. Files may become invisible to the game. |
| `-x LIST.txt` | Completely omit selected files or directories. |
| `-ref FOLDER|ARCHIVE` | Add a reference source for safe-hiding decisions and missing DDS lookup. Repeatable. |

Map sectors, `def/*.sii`, `unit/*.sii`, and `/locale` paths are hidden only when the same path exists in `original_game_paths.txt`, is provided through `-ref`, or is explicitly named by an input text file. **Every `.sui` file may be hidden regardless of its directory.** Verified sign, cargo, and vehicle-accessory families are also allowed automatically. Models, materials, and textures are normally reached through direct references and do not require directory-listing visibility.

The packer retains the `/def` listing and omits `/def/city` and `/def/company` listings selected by `-h` as safely redundant. Their file payloads remain in the archive and are addressable by hash.

The generated `original_game_paths.tar.gz` in the project root participates in the build and is not regenerated automatically by every build.

### GDeflate and DDS in HashFS v2

The GDeflate texture-record type is mandatory and has no disable switch. The packer parses binary TOBJ files, resolves their DDS references, converts supported legacy DDS surfaces to DX10 when necessary, and encodes every valid pair as one texture entry. `-cm=max`, `medium`, and `min` select DirectStorage BEST_RATIO, DEFAULT, and FASTEST respectively; `-cm=no` stores the surface raw inside the same texture-record type. If several TOBJs share one DDS, every logical pair remains independent, which is correct for the format.

If a DDS is absent from the inputs, the packer searches `-ref`. A found dependency is added to the correct output. If it remains unresolved, the TOBJ is stored separately without GDeflate and all missing paths are listed. The general recommendation to provide extracted game archives is printed only when no `-ref` source was supplied.

### eZIP and AEM

| Option | Description |
| --- | --- |
| `--entry-sort=enabled|disabled`, `-es=y|n` | Sort directories/files or randomly interleave them. Disabled by default. |
| `-a`, `-af`, `-ad` | Preserve empty files and explicit ZIP directory records. |
| `--junk="EOCD=1; FE=N"` | Create an incorrect EOCD and/or up to 10,000 false central-directory entries. |

With sorting disabled, an explicit directory is still written before its own descendants so archive viewers can retain its marker. FE places roughly one third of false entries near the beginning and distributes the remainder among complete genuine local entries.

### Entertainment and diagnostic records

`--junk="NAME=N; ..."` creates selected junk classes. Short names:

| Name | Full name | Format and limit |
| --- | --- | --- |
| `DH` | `DuplicateHash` | HashFS; up to 2 in v2 and 50,000 in v1. |
| `MOOB` | `MetadataOutOfBounds` | HashFS, up to 30. |
| `IM` | `InvalidMetadata` | HashFS, up to 30. |
| `POOB` | `PayloadOutOfBounds` | HashFS, up to 30. |
| `POT` | `PayloadOverlapsTable` | HashFS, up to 30. |
| `IZH` | `InvalidZlibHeader` | HashFS, up to 30. |
| `UC` | `UnknownChunk` | HashFS v2 only, up to 100. |
| `SCR` | `SuspiciousCompressionRatio` | HashFS, up to 50,000. |
| `RV` | `RandomValid` | Up to 1,000 correctly structured random entries with game extensions and sizes up to 15 MiB. |
| `DMD` | `DecoyMetadataDescriptor` | HashFS v2 only, 0 or 1: false descriptor blocks before recognized descriptors. |
| `EOCD` | `IncorrectEOCD` | eZIP/AEM, 0 or 1. |
| `FE` | `FalseEntries` | eZIP/AEM, up to 10,000. |

`--crash-game=ByManifest` / `-cg=BM` is available only for HashFS v1 and deliberately inflates the declared sizes of `manifest.sii`. The game will not start while such a mod is enabled. The GUI separates this option with a warning, and it is intended only for controlled experiments.

### Building

Python 3.11 or newer is required. Build dependencies are listed in `requirements-build.txt`.

```powershell
python .\build.py                    # native format for the current OS
python .\build.py --format exe       # Windows
python .\build.py --format deb       # Debian/Ubuntu
python .\build.py --format rpm       # RPM-based Linux
python .\build.py --format app       # macOS
```

`--skip-install` prevents installation of missing build dependencies. The Windows `build.ps1` wrapper creates `dist/UniversalSCSPacker.exe`. Packages for every platform are written directly to `dist`.

On Windows, the DirectStorage runtime libraries must be present beside the sources for HashFS v2 builds. 7z/RAR input is handled by `ArchiveBackend`. Their separate licenses and notices remain in the corresponding directories.

### License

The main project is distributed under the **GNU General Public License v2.0**; see [LICENSE](LICENSE). Third-party components retain their own terms in `DirectStorage` and `ArchiveBackend`.

This project is not affiliated with SCS Software. Euro Truck Simulator, American Truck Simulator, and SCS names are used only to describe compatibility.
