"""Readable Python reconstruction of SXC Packer's command-line archive writer.

Produces SCS HashFS v2 and v1 archives. It is format-compatible, but does not reproduce
the original executable's private padding, exact deflate bytes or GUI dialogs.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import tarfile
import time
import zlib
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from app_settings import configured_workers
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from cityhash_legacy import cityhash64
from DDSConverter import DDSConversionError, convert_to_dx10, is_dx10
from GDeflate import GDeflateError, compress as compress_gdeflate


PROGRAM_NAME = "UniversalSCSPacker.exe" if getattr(sys, "frozen", False) else "python packer.py"

USAGE = f"""
Universal SCS Packer - HashFS v2/v1, Ghostly eZIP and AEM archive writer

USAGE
  {PROGRAM_NAME} -i <input...> [-o <archive.scs...>] [options]

REQUIRED
  -i <source...>              One or more folders or archives. ZIP, 7z, RAR, TAR and
                               other formats supported by the bundled 7-Zip backend are
                               accepted. Archive type is detected from its contents, so
                               the filename extension is not important. With one output,
                               all sources are merged in order and a later source replaces
                               the same path from an earlier source.
                               Example: -i base.7z patch.rar local_folder

OUTPUT
  -o <file.scs...>            One or more independently quoted destination archives.
                               Inputs are divided into consecutive groups. Earlier outputs
                               receive ceil(input-count/output-count) inputs while enough
                               inputs are reserved for every remaining output.
                               Example: 7 inputs + 3 outputs => 3, 3 and 1 inputs.
                               DDS dependencies are copied into the output containing the
                               referencing TOBJ, even when they came from another input.
                               Default: <first-input>.scs in the current directory.
  Per-archive options:        Follow an output with -> and one quoted option string.
                               Example: -o one.scs -> "-fs=v2 -h=all"
                                           two.scs -> "-fs=ez -a"
                               Verbosity remains global and follows all output definitions.

PATH-LIST OPTIONS
  Every list is UTF-8 or UTF-16 (with BOM), one archive-relative path per line.
  A leading slash is optional; selecting a directory affects its descendants.

  -e <list.txt>, -e=all       Encrypt matching .sii files using the 3nK wrapper.
                               "all" encrypts every packed .sii file.
  -h <list.txt>, -h=all       Hide matching entries from directory listings.
                               "all" requests hiding for every packed path while
                               retaining the definition/map safety checks below.
                               The root listing and redundant original/reference
                               directory listings are omitted. Listings which
                               introduce a new visible name are retained.
  -fh, --force-hide LIST.txt  Force-hide paths from a text-list file, bypassing all
                               visibility checks. One archive-relative path per line.
                               A warning is always emitted when this option is used.
  -x <list.txt>               Exclude matching files and folders completely.
  -ref <folder|archive>       Dependency folder or archive used for safe hiding
                               decisions. It supports the same formats as -i.

ARCHIVE OPTIONS
  --file-system <TYPE>        Output format: hashfsv2 (default), hashfsv1,
                               ezip or aem.
  -fs=<v2|v1|ez|aem>          Short form of --file-system.
  --salt=<0..65535|0xNNNN>    HashFS path-hash salt. Default: random non-zero for
                               HashFS v2 and 0 for HashFS v1.
  -salt=<...|random>           Explicitly selects a salt. A salted HashFS v1
                               archive automatically creates the compatibility
                               ZIP stub_salt0.scs containing only /base/def/.
                               Enable the stub as a separate mod in any position.
                               This intentionally disables the game's
                               incompatible pooled hash lookup and enables per-pack lookup.
  -cm <max|medium|min|no>     Compression method. Default: max.
  --compression-method <...>  Long form of -cm:
                                 max    zlib level 9
                                 medium zlib level 6
                                 min    zlib level 1
                                 no     store file/directory payloads and HashFS v2
                                        texture surfaces without compression
                                        (index-table compression is controlled by -tm)
  --table-mode <MODE>          HashFS table mode.
                               HashFS v2:
                                 <N>t_<N>m_<compression>
                                 t = path table, m = metadata; components may be
                                 written in any order and underscores are optional.
                                 Position 1: offset 0x1000
                                 Position 2: end of archive
                                 Position 3: immediately after the v2 header (0x40)
                                 Position 4: after a randomly selected payload file
                                 Compression: compressed/c, uncompressed/u,
                                 uncompressed_metadata/um, uncompressed_table/ut,
                                 compressed_metadata/cm, compressed_table/ct.
                                 Example: 1t_2m_uncompressed_m or 1t2mum.
                                 random, r randomly selects all table parameters.
                                 Default: 2t_2m_compressed.
                                 In the default layout payload starts at 0x40,
                                 immediately after the aligned v2 header.
                               HashFS v1 (table placement):
                                 1 - offset 0x1000 (default)
                                 2 - end of archive
                                 3 - offset 0x20
                                 4 - after a randomly selected file
  -tm=<MODE>                   Short form of --table-mode.
  -af                         Keep zero-byte files. They are skipped by default.
  -ad                         Preserve empty directories. HashFS v1/v2 store empty
                               directory listings; eZIP/AEM add explicit ZIP entries.
                               For HashFS, -h disables -ad so hidden paths stay hidden.
  -a                          Equivalent to -af -ad for every output format.
  --entry-sort <enabled|disabled>
  -es=<y|n>                   eZIP/AEM entry ordering. Enabled writes
                               directories first and sorts both groups by path.
                               Disabled (default) randomly interleaves directory
                               and file entries; ZIP readers and the game do not
                               require either kind to appear first. Each explicit
                               directory still precedes its own descendants so
                               archive viewers retain its encryption marker.
  --sort-payload <enabled|disabled>
  -spl=<y|n>                  HashFS v1/v2 payload order. Enabled (default)
                               randomizes physical payload placement while entry
                               tables remain correctly hash-sorted. Disabled keeps
                               the collected source order.
  --dry-run                   Read, merge and compress all inputs, but do not create or
                               replace the destination archive. Reports the exact planned
                               entry count, payload size and estimated archive size.
  --junk="NAME=N; ..."       Add entertainment/junk records.
                               Short names: DH=DuplicateHash, MOOB=MetadataOutOfBounds,
                               IM=InvalidMetadata, POOB=PayloadOutOfBounds,
                               POT=PayloadOverlapsTable, IZH=InvalidZlibHeader,
                               UC=UnknownChunk (v2 only), SCR=SuspiciousCompressionRatio,
                               RV=RandomValid and DMD=DecoyMetadataDescriptor (0 or 1,
                               HashFS v2 only). EOCD=IncorrectEOCD (0 or 1) writes
                               an obfuscated false ZIP end record for eZIP/AEM.
                               FE=FalseEntries adds 0..10000 unreferenced, believable
                               central-directory rows to eZIP/AEM without payloads:
                               about one third near the start and the rest randomly
                               distributed after complete genuine local entries.
                               Ordinary malformed kinds allow 30; UC allows 100;
                               SCR allows 50000; RV allows 1000. Example:
                               --junk="POOB=5; IM=3; UC=10; RV=100; DMD=1"
  --crash-game=ByManifest    HashFS v1 only. Deliberately inflate both declared
  -cg=BM                     sizes of manifest.sii so the game will no longer
                               start with this mod.

PERFORMANCE
  File reading, optional 3nK processing and zlib compression always run in
  parallel. The worker count is selected automatically from the CPU count.

OUTPUT DETAIL
  --verbose=<min|bar|max>     Full verbosity option. min prints phases and summary,
                               bar adds progress bars, max prints every entry.
  --verbose                   Same as --verbose=max.
  -v0                         Phase messages and final summary only.
  -v1                         Phase messages plus a live writing progress bar.
                               This is the default.
  -v2, -v                     Maximum detail: print every archive entry as written.

HIDING SAFETY
  Map sector files, def/*.sii files, unit/*.sii files, and files below /locale
  are never hidden unless the same path is available from -ref or
  original_game_paths.txt, or that exact path is explicitly referenced by an
  input text file. Every .sui file is always safe to hide, regardless of its
  directory.
  Proven path-addressed sign, cargo and vehicle-accessory families are always
  safe to hide when selected by -h and are reported during packing.
  Models, materials and textures need no such visibility rule.
  Reference files are neither packed nor compared by contents, except that a
  DDS is copied when a TOBJ from the input explicitly references that missing
  texture. This makes a random-salt HashFS v2 archive self-contained.
  List entries are recursive selectors: "automat/" includes all of /automat,
  and "material/model/" includes all of /material/model.

OTHER
  --help, -?                  Show this help. (-h is reserved for hide lists.)

WINDOWS PATHS
  Paths containing spaces may be quoted. Do not end a quoted Windows path with
  one backslash: use ".\\My folder" instead of ".\\My folder\\". The packer
  also repairs this common PowerShell quoting form when possible.

HASHFS V2
  HashFS v2 uses compressed or uncompressed entry/metadata tables, binary directory listings,
  16-byte payload alignment and typed metadata. Valid binary TOBJ files and their
  referenced DDS textures are always converted to DX10 and packed into one
  mandatory GDeflate texture entry using Microsoft DirectStorage. Unmappable DDS
  formats stop packing rather than creating an invalid v2 archive. HashFS v2
  supports --salt. --table-mode independently controls entry/metadata table
  placement and compression.

GHOSTLY EZIP / AEM
  These formats accept the common -i/-o options plus -a, -af, -ad,
  --entry-sort/-es, --junk="EOCD=1; FE=N" and all verbosity forms. FE adds up to
  10000 unreferenced false paths; other entertainment records are HashFS-only.
  -v and bare --verbose select max.
  Ghostly eZIP stores normal plaintext Deflate payloads while setting the ZIP
  encryption flag in each local and central entry header. AEM uses metadata-free
  AEM! local records and keeps sizes, codecs and names authoritative in its
  obfuscated ZIP-like central directory.

The destination is written to a temporary file in its own folder, then moved
into place only after writing completes.
""".strip()

SECTOR_NAME = re.compile(r"sec[+-]\d+[+-]\d+\.[^./]+\Z", re.IGNORECASE)
ALL_SELECTOR = "\0ALL\0"
QUOTED_VALUE = re.compile(r'''["']([^"'\r\n]+)["']''')
UNQUOTED_PATH = re.compile(r"/?[A-Za-z0-9_+./\\-]+/[A-Za-z0-9_+./\\-]+")
TEXT_REFERENCE_EXTENSIONS = {
    ".cfg", ".font", ".ini", ".json", ".mat", ".sii", ".soundref",
    ".sui", ".txt", ".xml",
}

# Files in these definition families are addressed by explicit path tables,
# not discovered through HashFS directory listings. If the user selected them
# with -h, keeping their names visible serves no purpose. Keep these rules
# evidence-based; partially covered vehicle trees stay under normal checks.
ALWAYS_HIDDEN_EXACT_CATEGORIES = {
    "def/sign/atlas",
    "def/sign/board",
    "def/sign/frame",
    "def/sign/template",
}

# These accessory directory names occur below individual truck definitions.
# Every matching original-game .sii is referenced by path from another .sii.
ALWAYS_HIDDEN_TRUCK_ACCESSORY_CATEGORIES = {
    "air_tank", "badge", "badge_a", "badge_b", "badge_c", "c_badge",
    "chs_badges", "f_chs_logo", "f_fendr_chs", "f_inlay_cab",
    "f_intk_b_chs", "f_light_mid", "f_logo", "f_turn_light", "filter",
    "int_display", "intlight_bck", "intlight_bgr", "r_chs_cover",
    "r_light", "s_mirror",
}

# The ferry connection units are not included by a literal path from
# def/ferry.sii.  Prism3D enumerates /def/ferry/connection/ instead, so hiding
# that listing makes every added ferry route disappear from the mod.
DIRECTORY_DISCOVERED_DEFINITION_PREFIXES = (
    "def/ferry/connection/",
)

# Definition-tree hiding policy. Keep the /def listing itself so the game can
# enter the definition tree, but omit the two child listings whose file paths
# are resolved explicitly. This affects directory metadata only; matching file
# payloads remain in the archive.
PRESERVED_DEFINITION_LISTINGS = {
    "def",
}
OMITTED_DEFINITION_LISTINGS = {
    "def/city",
    "def/company",
}

def runtime_directory() -> Path:
    """Return the script directory or PyInstaller's unpacked data directory."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def unpack_bundled_game_paths(bundle: Path) -> Path:
    """Extract the bundled list safely; the raw 4 MB list stays out of the EXE."""
    target_directory = Path(tempfile.gettempdir()) / "Universal SCS Packer"
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / "original_game_paths.txt"
    with tarfile.open(bundle, "r:gz") as archive:
        try:
            member = archive.getmember("original_game_paths.txt")
        except KeyError as exc:
            raise ValueError(f"Bundled game-path archive is invalid: {bundle}") from exc
        if not member.isfile():
            raise ValueError(f"Bundled game-path archive contains an invalid entry: {bundle}")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"Unable to read bundled game-path archive: {bundle}")
        try:
            data = source.read()
        finally:
            source.close()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=target_directory, prefix=".game_paths_",
                suffix=".tmp", delete=False) as stream:
            temporary_name = stream.name
            stream.write(data)
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return target


def default_game_paths_file() -> Path:
    directory = runtime_directory()
    raw_list = directory / "original_game_paths.txt"
    if raw_list.is_file():
        return raw_list
    bundle = directory / "original_game_paths.tar.gz"
    if bundle.is_file():
        return unpack_bundled_game_paths(bundle)
    return raw_list


DEFAULT_GAME_PATHS_FILE = default_game_paths_file()
GUI_PROGRESS_DETAIL_PREFIX = "\x1dPROGRESS_DETAIL\x1f"


def emit_early_message(level: str, text: str, *, stream=None) -> None:
    """Emit a classified message before Reporter has been constructed."""
    stream = sys.stderr if stream is None else stream
    normalized = level.upper()
    if os.environ.get("UNIVERSAL_SCS_GUI_LEVELS") == "1":
        print(f"\x1e{normalized}\x1f{text}", file=stream)
    else:
        print(f"{normalized}: {text}", file=stream)


def format_kilobytes(size: int) -> str:
    return f"{size / 1024:.2f} KB"


class Reporter:
    """Keeps normal messages, progress, and per-entry output coherent."""

    def __init__(self, level: int) -> None:
        self.level = level
        self._progress_visible = False
        self._gui_levels = os.environ.get("UNIVERSAL_SCS_GUI_LEVELS") == "1"

    @staticmethod
    def _message_level(text: str) -> str:
        value = text.lstrip()
        upper = value.upper()
        if upper.startswith(("WARNING:", "WARN:")):
            return "WARNING"
        if upper.startswith(("ERROR:", "ERR:")):
            return "ERROR"
        if upper.startswith("SUCCESS:"):
            return "SUCCESS"
        if value.startswith(("Universal SCS Packer", "Inputs:", "Outputs:",
                             "Options:", "Selectors:", "Parallel ",
                             "Parallel HashFS", "GDEFLATE FALLBACK",
                             "WRITE ",
                             "Definition categories ", "Protected categories ", "Hide safety:",
                             "Redundant root/")) or text.startswith("  "):
            return "SUCCESS" if value.startswith("WRITE ") else "DEBUG"
        if (value.startswith(("DDS conversion:", "GDeflate textures:",
                              "Source scan complete:", "Added ", "DRY RUN:"))
                or " completed" in value.casefold()
                or " created:" in value.casefold()):
            return "SUCCESS"
        return "INFO"

    @staticmethod
    def _without_visible_level(text: str) -> str:
        stripped = text.lstrip()
        indentation = text[:len(text) - len(stripped)]
        for prefix in ("SUCCESS:", "WARNING:", "WARN:", "ERROR:", "ERR:"):
            if stripped.upper().startswith(prefix):
                return indentation + stripped[len(prefix):].lstrip()
        return text

    def _finish_progress_line(self) -> None:
        if self._progress_visible:
            print()
            self._progress_visible = False

    def message(self, text: str) -> None:
        self._finish_progress_line()
        if not self._gui_levels:
            print(text)
            return
        level = self._message_level(text)
        visible = self._without_visible_level(text)
        marker = f"\x1e{level}\x1f"
        print("\n".join(marker + line for line in visible.split("\n")))

    def progress(self, label: str, current: int, total: int,
                 subject: str | None = None) -> None:
        if self.level >= 2:
            # Verbose mode keeps the same per-item detail that the progress
            # bar represents in v1.  The GUI collects these lines into a
            # collapsible group instead of rendering thousands of rows at once.
            subject = subject or "(unknown)"
            total = max(1, total)
            detail = f"DETAIL {label} | {subject} | {current}/{total}"
            if self._gui_levels:
                print(f"{GUI_PROGRESS_DETAIL_PREFIX}{label}\x1f{current}"
                      f"\x1f{total}\x1f{detail}")
            else:
                self.message(detail)
            return
        if self.level != 1:
            return
        total = max(1, total)
        fraction = min(1.0, current / total)
        width = 28
        filled = int(width * fraction)
        bar = "#" * filled + "-" * (width - filled)
        print(f"\r{label}: [{bar}] {current}/{total} ({fraction:.0%})",
              end="", flush=True)
        self._progress_visible = True

    def finish_progress(self) -> None:
        self._finish_progress_line()

    def write_entry(self, record: "Record", current: int, total: int,
                    file_system: str = "HashFS v1") -> None:
        if self.level == 1:
            self.progress(f"Writing {file_system}", current, total)
        elif self.level >= 2:
            kind = "directory" if record.flags & 1 else "file"
            detail = (f"WRITE {current:>6}/{total:<6} /{record.path} "
                      f"[{kind}; {format_kilobytes(len(record.payload))}]")
            if self._gui_levels:
                label = f"Writing {file_system}"
                print(f"{GUI_PROGRESS_DETAIL_PREFIX}{label}\x1f{current}"
                      f"\x1f{max(1, total)}\x1f{detail}")
            else:
                self.message(detail)


def normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def hash_archive_path(path: str, salt: int = 0) -> int:
    return cityhash64(((str(salt) if salt else "") + path).encode("utf-8"))


def read_list(filename: Path) -> set[str]:
    if not filename.is_file():
        emit_early_message("WARNING", f"List file not found: {filename}")
        return set()
    raw = filename.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        decoded = raw.decode("utf-16")
    else:
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = raw.decode("cp1252")
    if "\x00" in decoded:
        raise ValueError(f"List file has NUL characters or unsupported encoding: {filename}")
    lines = decoded.splitlines()
    return {normalize_path(line) for line in lines if line.strip()}


PATH_ARGUMENTS = {"-i", "-o", "-e", "-h", "-x", "-ref", "-fh", "--force-hide"}
EMBEDDED_OPTION = re.compile(
    r"\s+(-v[012]?|--verbose(?:=[^\s]+)?|-a(?:f|d)?|-i|-o|-e(?:=[^\s]+)?|-h(?:=[^\s]+)?|-x|-ref|"
    r"-fh(?:=[^\s]+)?|--force-hide(?:=[^\s]+)?|-tm(?:=[^\s]+)?|-cm|-\?|"
    r"--help|--dry-run|--table-mode(?:=[^\s]+)?|--compression-method(?:=[^\s]+)?|"
    r"--file-system(?:=[^\s]+)?|-fs(?:=[^\s]+)?|"
    r"--junk-(?:duplicate-hash|metadata-out-of-bounds|invalid-metadata|"
    r"payload-out-of-bounds|payload-overlaps-table|invalid-zlib-header|"
    r"unknown-chunk|suspicious-compression|random-valid|false-entries)(?:=[^\s]+)?|"
    r"--junk(?:=[^\s]+)?|"
    r"--crash-game(?:=[^\s]+)?|-cg(?:=[^\s]+)?|"
    r"--?salt=[^\s]+)(?=\s|$)")


def split_embedded_options(value: str) -> list[str]:
    """Separate known options swallowed into a path by broken Windows quoting."""
    result: list[str] = []
    position = 0
    for match in EMBEDDED_OPTION.finditer(value):
        prefix = value[position:match.start()].rstrip()
        if prefix:
            result.append(prefix)
        result.append(match.group(1))
        position = match.end()
    suffix = value[position:].strip()
    if suffix:
        result.append(suffix)
    return result


def repair_windows_trailing_backslash_quoting(argv: list[str]) -> list[str]:
    """Repair argv mangled by a quoted Windows path ending in one backslash.

    PowerShell converts single quotes to Windows double quotes before launching
    an EXE.  A final backslash escapes that closing quote in the Microsoft argv
    rules, so the following option is swallowed into the same argument.  Keep
    ordinary argv untouched; only split an embedded quote after a path option.
    """
    repaired: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        repaired.append(argument)
        if argument in PATH_ARGUMENTS and index + 1 < len(argv):
            value = argv[index + 1]
            if '"' in value:
                path_value, remainder = value.split('"', 1)
                repaired.append(path_value)
                if remainder.strip():
                    option, _, option_value = remainder.strip().partition(" ")
                    repaired.append(option)
                    if option_value.strip():
                        repaired.extend(split_embedded_options(option_value))
                for remaining in argv[index + 2:]:
                    repaired.extend(split_embedded_options(remaining))
                break
            repaired.append(value)
            index += 2
            continue
        index += 1
    return repaired


def path_argument(argv: list[str], index: int, option: str) -> tuple[Path, int]:
    """Read a path, accepting repaired unquoted pieces separated by spaces."""
    start = index + 1
    if start >= len(argv) or argv[start].startswith("-"):
        raise ValueError(f"{option} requires a path in this command-line version")
    pieces = [argv[start]]
    index = start + 1
    while index < len(argv) and not argv[index].startswith("-"):
        pieces.append(argv[index])
        index += 1
    return Path(" ".join(pieces)), index


def input_arguments(argv: list[str], index: int) -> tuple[list[Path], int]:
    """Read every independently quoted folder/archive following one ``-i``."""
    index += 1
    sources: list[Path] = []
    while index < len(argv) and not argv[index].startswith("-"):
        sources.append(Path(argv[index]))
        index += 1
    if not sources:
        raise ValueError("-i requires at least one folder or supported archive")
    return sources, index


def output_arguments(argv: list[str], index: int) -> tuple[list[Path], int]:
    """Read every independently quoted destination following one ``-o``."""
    index += 1
    destinations: list[Path] = []
    while index < len(argv) and not argv[index].startswith("-"):
        destinations.append(Path(argv[index]))
        index += 1
    if not destinations:
        raise ValueError("-o requires at least one output archive path")
    return destinations, index


def configured_output_arguments(
        argv: list[str], index: int) -> tuple[list[Path], list[str | None], int]:
    """Read ``-o FILE [-> 'OPTIONS']...`` output specifications."""
    index += 1
    destinations: list[Path] = []
    specifications: list[str | None] = []
    while index < len(argv):
        value = argv[index]
        if value == "->":
            if not destinations or specifications[-1] is not None:
                raise ValueError("'->' must follow exactly one output archive")
            if index + 1 >= len(argv):
                raise ValueError("'->' requires one quoted archive-option string")
            option_string = argv[index + 1]
            if not option_string.strip():
                raise ValueError("Per-archive option string cannot be empty")
            specifications[-1] = option_string
            index += 2
            continue
        if value.startswith("-"):
            break
        destinations.append(Path(value))
        specifications.append(None)
        index += 1
    if not destinations:
        raise ValueError("-o requires at least one output archive path")
    return destinations, specifications, index


def all_or_list_selection(value: str, option: str) -> set[str]:
    """Read a normal selector list, with ``all`` reserved for -h/-e."""
    if value.strip().lower() == "all":
        return {ALL_SELECTOR}
    return read_list(Path(value))


def force_hide_selection(value: str) -> set[str]:
    """Read force-hide selectors exclusively from a path-list file."""
    value = value.strip()
    if not value:
        raise ValueError("--force-hide requires a path-list file")
    if value.lower() == "all":
        raise ValueError("Use -h=all instead of --force-hide=all")
    candidate = Path(value)
    if not candidate.is_file():
        raise ValueError(f"--force-hide list file not found: {candidate}")
    selectors = read_list(candidate)
    if not selectors:
        raise ValueError("--force-hide list contains no archive paths")
    return selectors


def extract_zip_source(source: Path, destination: Path) -> None:
    """Extract an ordinary ZIP to a private working folder without Zip Slip."""
    try:
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                raw_name = entry.filename.replace("\\", "/")
                relative = PurePosixPath(raw_name)
                if (not raw_name or relative.is_absolute() or ".." in relative.parts):
                    raise ValueError(f"Unsafe ZIP entry in {source.name}: {entry.filename!r}")
                if entry.is_dir():
                    target = destination.joinpath(*relative.parts)
                    target.mkdir(parents=True, exist_ok=True)
                    try:
                        timestamp = time.mktime((*entry.date_time, 0, 0, -1))
                        os.utime(target, (timestamp, timestamp))
                    except (OSError, OverflowError, ValueError):
                        pass
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry, "r") as input_stream, target.open("wb") as output_stream:
                    while chunk := input_stream.read(1024 * 1024):
                        output_stream.write(chunk)
                try:
                    timestamp = time.mktime((*entry.date_time, 0, 0, -1))
                    os.utime(target, (timestamp, timestamp))
                except (OSError, OverflowError, ValueError):
                    pass
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP archive: {source}") from exc


def extract_tar_source(source: Path, destination: Path) -> None:
    """Extract a TAR-family archive while rejecting links and path traversal."""
    try:
        with tarfile.open(source, "r:*") as archive:
            for entry in archive.getmembers():
                raw_name = entry.name.replace("\\", "/")
                relative = PurePosixPath(raw_name)
                if (not raw_name or relative.is_absolute() or ".." in relative.parts
                        or re.match(r"^[A-Za-z]:", raw_name)):
                    raise ValueError(f"Unsafe TAR entry in {source.name}: {entry.name!r}")
                if entry.issym() or entry.islnk():
                    raise ValueError(f"Archive links are not supported in {source.name}: {entry.name!r}")
                if entry.isdir():
                    target = destination.joinpath(*relative.parts)
                    target.mkdir(parents=True, exist_ok=True)
                    try:
                        os.utime(target, (entry.mtime, entry.mtime))
                    except (OSError, OverflowError, ValueError):
                        pass
                    continue
                if not entry.isfile():
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                input_stream = archive.extractfile(entry)
                if input_stream is None:
                    raise ValueError(f"Unable to read TAR entry in {source.name}: {entry.name!r}")
                with input_stream, target.open("wb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                try:
                    os.utime(target, (entry.mtime, entry.mtime))
                except (OSError, OverflowError, ValueError):
                    pass
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"Invalid or unsupported TAR archive: {source}") from exc


def seven_zip_executable() -> Path | None:
    """Locate the bundled 7-Zip console backend, then a system installation."""
    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))
    roots.extend((Path(__file__).resolve().parent, Path(sys.executable).resolve().parent))
    for root in roots:
        for relative in (Path("ArchiveBackend/7z.exe"), Path("7z.exe")):
            candidate = root / relative
            if candidate.is_file():
                return candidate
    installed = shutil.which("7z") or shutil.which("7zz")
    if installed:
        return Path(installed)
    conventional = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    return conventional if conventional.is_file() else None


def _seven_zip(arguments: list[str], source: Path) -> subprocess.CompletedProcess[str]:
    executable = seven_zip_executable()
    if executable is None:
        raise ValueError(
            f"Cannot read archive {source}: the bundled 7-Zip backend is missing")
    command = [str(executable), *arguments]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        startupinfo=startupinfo, creationflags=creationflags, check=False)


def seven_zip_members(source: Path) -> list[str]:
    """List and validate paths before allowing 7-Zip to extract an archive."""
    result = _seven_zip(["l", "-slt", "-ba", "-sccUTF-8", str(source.resolve())], source)
    if result.returncode != 0:
        detail = next((line.strip() for line in reversed(result.stdout.splitlines())
                       if line.strip()), "unknown archive error")
        raise ValueError(f"Invalid, encrypted or unsupported archive {source}: {detail}")
    members: list[str] = []
    records = re.split(r"\r?\n\s*\r?\n", result.stdout.strip())
    for record in records:
        properties = {}
        for line in record.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                properties[key] = value
        if "Path" not in properties:
            continue
        raw_name = properties["Path"].replace("\\", "/")
        relative = PurePosixPath(raw_name)
        if (not raw_name or relative.is_absolute() or ".." in relative.parts
                or re.match(r"^[A-Za-z]:", raw_name)):
            raise ValueError(f"Unsafe archive entry in {source.name}: {raw_name!r}")
        if properties.get("Symbolic Link") or properties.get("Hard Link"):
            raise ValueError(f"Archive links are not supported in {source.name}: {raw_name!r}")
        members.append(raw_name)
    if not members:
        raise ValueError(f"Archive contains no readable entries: {source}")
    return members


def extract_seven_zip_source(source: Path, destination: Path) -> None:
    """Extract any archive format understood by the bundled 7-Zip backend."""
    seven_zip_members(source)
    result = _seven_zip([
        "x", "-y", "-bd", "-bb0", "-sccUTF-8", f"-o{destination}",
        str(source.resolve()),
    ], source)
    if result.returncode != 0:
        detail = next((line.strip() for line in reversed(result.stdout.splitlines())
                       if line.strip()), "unknown extraction error")
        raise ValueError(f"Unable to extract archive {source}: {detail}")


def materialize_source(source: Path, role: str, temporary: ExitStack,
                       reporter: Reporter | None = None) -> Path:
    """Return a directory for a folder or supported archive source."""
    if source.is_dir():
        return source
    if source.is_file():
        workspace = Path(temporary.enter_context(
            tempfile.TemporaryDirectory(prefix="universal_scs_packer_")))
        if zipfile.is_zipfile(source):
            archive_kind = "ZIP"
            extractor = extract_zip_source
        elif tarfile.is_tarfile(source):
            archive_kind = "TAR"
            extractor = extract_tar_source
        else:
            archive_kind = "archive"
            extractor = extract_seven_zip_source
        message = f"Reading {role} {archive_kind}: {source}"
        if reporter is None:
            print(message)
        else:
            reporter.message(message)
        extractor(source, workspace)
        return workspace
    raise ValueError(
        f"{role.capitalize()} must be a folder or a supported archive: {source}")


def is_selected(path: str, selections: set[str]) -> bool:
    if ALL_SELECTOR in selections:
        return True
    if path in selections:
        return True
    parts = path.split("/")
    return any("/".join(parts[:index]) in selections for index in range(1, len(parts)))


def protected_from_hiding(path: str) -> bool:
    parts = path.split("/")
    return ((len(parts) >= 2 and parts[0].lower() == "def"
             and Path(path).suffix.lower() in (".sii", ".sui"))
            or (len(parts) >= 2 and parts[0].lower() == "unit"
                and Path(path).suffix.lower() in (".sii", ".sui"))
            or (len(parts) >= 2 and parts[0].lower() == "locale")
            or (len(parts) >= 3 and parts[0].lower() == "map"
                and SECTOR_NAME.fullmatch(parts[-1]) is not None))


def requires_directory_discovery(path: str) -> bool:
    """Whether the game discovers this definition via a directory listing."""
    normalized = normalize_path(path).casefold()
    return any(normalized.startswith(prefix)
               for prefix in DIRECTORY_DISCOVERED_DEFINITION_PREFIXES)


def overlay_file_sources(roots: Path | list[Path]) -> dict[str, Path]:
    """Return the final path-to-source map for an ordered source overlay."""
    source_roots = [roots] if isinstance(roots, Path) else roots
    paths: dict[str, Path] = {}
    for root in source_roots:
        for directory, directories, files in os.walk(root, followlinks=False):
            directories.sort()
            relative_dir = Path(directory).relative_to(root)
            for filename in sorted(files):
                relative = ((relative_dir / filename).as_posix()
                            if relative_dir != Path(".") else filename)
                relative = unwrap_input_package_path(relative)
                # Assignment is intentional: later -i sources override earlier
                # folders and ZIPs without copying either source tree.
                paths[relative] = Path(directory) / filename
    return paths


def unwrap_input_package_path(path: str) -> str:
    """Flatten an input base/DLC mount into normal SCS-relative paths.

    Some source archives are supplied as mounted package trees such as
    ``base/def/city.sii`` or ``dlc_oversize/def/route.sii``.  HashFS mods need
    the actual archive paths below that mount, so each individual -i source is
    overlaid after removing precisely one such leading directory.
    """
    first, separator, remainder = path.partition('/')
    if separator and (first.casefold() == 'base' or first.casefold().startswith('dlc_')):
        return remainder
    return path


def file_paths(roots: Path | list[Path]) -> set[str]:
    return set(overlay_file_sources(roots))


def without_comments(line: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote:
            escaped = True
        elif char in ('"', "'"):
            if not quote:
                quote = char
            elif char == quote:
                quote = ""
        elif not quote and (char == "#" or line[index:index + 2] == "//"):
            return line[:index]
    return line


def definition_referenced_paths(roots: list[Path], targets: set[str]) -> set[str]:
    """Resolve protected paths explicitly written in input text files.

    Only the mod inputs are scanned. A reference archive grants visibility by
    containing the same target path, never by mentioning it in unrelated text.
    """
    if not targets:
        return set()
    # Scan formats which can meaningfully contain literal archive paths.  Skip
    # binary model/texture payloads without opening them.
    definition_sources = {
        path: source for path, source in overlay_file_sources(roots).items()
        if Path(path).suffix.lower() in TEXT_REFERENCE_EXTENSIONS
        and source.stat().st_size <= 64 * 1024 * 1024
    }
    definitions = list(definition_sources.items())

    def scan(job: tuple[str, Path]) -> set[str]:
        source_path, source = job
        found: set[str] = set()
        parent = posixpath.dirname(source_path) or "."
        raw = source.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            content = raw.decode("utf-16", errors="replace")
        elif b"\x00" in raw:
            return found
        else:
            content = raw.decode("utf-8-sig", errors="replace")
        for line in content.splitlines():
            line = without_comments(line)
            tokens = [match.group(1) for match in QUOTED_VALUE.finditer(line)]
            tokens.extend(match.group() for match in UNQUOTED_PATH.finditer(line))
            for token in tokens:
                token = token.strip().replace("\\", "/")
                if not token or ":" in token:
                    continue
                candidates = [token.lstrip("/")]
                if not token.startswith("/") and parent != ".":
                    candidates.append(posixpath.normpath(parent + "/" + token))
                found.update(candidate for candidate in candidates
                             if candidate in targets and candidate != source_path)
        return found

    found: set[str] = set()
    workers = configured_workers(max(1, min(32, os.cpu_count() or 1)))
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="sii-path-scan") as executor:
        for matches in executor.map(scan, definitions):
            found.update(matches)
    return found


def directory_paths(root: Path | None) -> set[str]:
    """Return every archive-relative directory physically present below root."""
    if root is None:
        return set()
    result = {""}
    for directory, directories, _files in os.walk(root, followlinks=False):
        directories.sort()
        relative = Path(directory).relative_to(root)
        if relative != Path("."):
            unwrapped = unwrap_input_package_path(relative.as_posix())
            if unwrapped:
                result.add(unwrapped)
    return result


def definition_category(path: str) -> str | None:
    """Return a compact category name suitable for a manual safety review."""
    parts = path.split("/")
    if not parts or parts[0].lower() not in ("def", "unit"):
        return None
    root = parts[0].lower()
    return f"/{root}" if len(parts) < 3 else f"/{root}/{parts[1]}"


def mandatory_definition_category(path: str) -> str | None:
    """Return a proven path-addressed category which never needs listing visibility."""
    normalized = normalize_path(path).lower()
    parent = posixpath.dirname(normalized)
    extension = PurePosixPath(normalized).suffix
    if extension == ".sui":
        return "/**/*.sui"
    if extension == ".sii" and parent in ALWAYS_HIDDEN_EXACT_CATEGORIES:
        return "/" + parent + "/*.sii"
    if (extension == ".sii" and normalized.startswith("def/vehicle/truck/")
            and posixpath.basename(parent) in ALWAYS_HIDDEN_TRUCK_ACCESSORY_CATEGORIES):
        return ("/def/vehicle/truck/**/"
                + posixpath.basename(parent) + "/*.sii")
    if (extension == ".sii" and normalized.startswith("def/vehicle/trailer/")
            and posixpath.basename(parent) == "special_paint_job"):
        return "/def/vehicle/trailer/**/special_paint_job/*.sii"
    return None


def apply_definition_listing_policy(hidden: set[str], visible: set[str],
                                    pruned_directories: set[str]) -> None:
    """Apply the verified directory-listing policy for definition files."""
    for directory in PRESERVED_DEFINITION_LISTINGS:
        visible.add(directory)
        pruned_directories.discard(directory)
    pruned_directories.update(
        directory for directory in OMITTED_DEFINITION_LISTINGS
        if is_selected(directory, hidden)
    )


def visible_protected_paths(roots: list[Path], hidden: set[str], reference: Path | None,
                            game_paths: set[str] | None = None
                            ) -> tuple[set[str], set[str], int, int, int, int,
                                       set[str], set[str]]:
    source_paths = file_paths(roots)
    reference_paths = file_paths(reference) if reference is not None else set()
    protected = {path for path in source_paths
                 if protected_from_hiding(path) and is_selected(path, hidden)}
    definition_referenced = definition_referenced_paths(roots, protected)
    mandatory_allowed = {
        path for path in protected if mandatory_definition_category(path) is not None
    }
    mandatory_categories = {
        category for path in mandatory_allowed
        if (category := mandatory_definition_category(path)) is not None
    }
    reference_allowed = (protected - mandatory_allowed) & reference_paths
    game_allowed = (protected - mandatory_allowed - reference_allowed) & (game_paths or set())
    remaining = protected - mandatory_allowed - reference_allowed - game_allowed
    definition_allowed = remaining & definition_referenced
    allowed_categories = {
        category for path in definition_referenced
        if (category := definition_category(path)) is not None
    }
    # Do not hide units which the game enumerates from a folder.  In
    # particular, ferry connections have no explicit @include from ferry.sii;
    # hiding only their files or their connection folder makes all routes
    # silently disappear.
    directory_discovered = {
        path for path in protected if requires_directory_discovery(path)
    }
    visible_files = (remaining - definition_allowed) | directory_discovered
    visible: set[str] = set()
    for path in visible_files:
        parts = path.split("/")
        visible.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))

    # An entire directory listing is redundant when every file below it is
    # selected for hiding and has the same path in the dependency reference.
    directory_candidates: set[str] = set()
    directory_blockers: set[str] = set()
    if reference is not None:
        for path in source_paths:
            parts = path.split("/")
            ancestors = {"/".join(parts[:index]) for index in range(1, len(parts))}
            directory_candidates.update(ancestors)
            if path not in reference_paths or not is_selected(path, hidden):
                directory_blockers.update(ancestors)
    pruned_directories = directory_candidates - directory_blockers

    # The game and dependency reference already supply many directory nodes.
    # Their listings can be omitted only if the mod does not add a visible
    # immediate child there.  The root itself stays omitted: standard top-level
    # nodes are supplied by the game, matching established protected mods.
    game_directories = {""}
    for path in game_paths or ():
        parts = normalize_path(path).split("/")
        game_directories.update(
            "/".join(parts[:index]) for index in range(1, len(parts)))
    reference_directories = directory_paths(reference)
    known_nodes = set(game_paths or ()) | game_directories
    known_nodes.update(reference_paths)
    known_nodes.update(reference_directories)
    pruned_directories.update(game_directories | reference_directories)

    # Include unselected files as well as protected files deliberately kept
    # visible by the safety pass.  For every new child name, retain precisely
    # the parent listing that introduces it; higher standard parents remain
    # supplied by the base game/reference archive.
    listing_visible_files = {
        path for path in source_paths if not is_hidden(path, hidden, visible)
    }
    required_listings: set[str] = set()
    for path in listing_visible_files:
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            child = "/".join(parts[:index])
            if child not in known_nodes:
                required_listings.add("/".join(parts[:index - 1]))
    pruned_directories.difference_update(required_listings - {""})

    # Apply the verified policy after the general safety pass so later pruning
    # cannot remove /def again. A selective -h is respected: city/company are
    # omitted only when their paths were actually selected.
    apply_definition_listing_policy(hidden, visible, pruned_directories)
    pruned_directories.add("")
    return (visible, pruned_directories, len(mandatory_allowed),
            len(reference_allowed), len(game_allowed), len(definition_allowed),
            mandatory_categories, allowed_categories)


def is_hidden(path: str, hidden: set[str], visible: set[str]) -> bool:
    return path not in visible and is_selected(path, hidden)


def encode_3nk(data: bytes) -> bytes:
    # Key schedule follows scs_tools/src/sii/nk3.rs (MIT).
    seed = secrets.randbelow(256)
    def key(position: int) -> int:
        index = (seed + position) & 255
        return ((((index << 2) ^ (index ^ 255)) << 3) ^ index) & 255
    return b"3nK\x01\x00" + bytes([seed]) + bytes(value ^ key(i) for i, value in enumerate(data))


@dataclass
class Item:
    path: str
    source: Path
    directory: bool
    listing: bytes = b""
    preserve_empty_directory: bool = False


@dataclass
class Record:
    path: str
    hash: int
    payload: bytes
    flags: int
    crc: int
    original_size: int
    offset: int = 0
    mtime: float | None = None
    junk_kind: str | None = None
    break_sizes: bool = False


@dataclass
class V2Record:
    path: str
    hash: int
    payload: bytes
    original_size: int
    compressed: bool
    directory: bool = False
    texture_meta: tuple[int, int, int, int] | None = None
    pmg: bool = False
    offset: int = 0
    junk_kind: str | None = None
    decoy_descriptor: bool = False

    @property
    def flags(self) -> int:
        return 1 if self.directory else 0


@dataclass
class JunkOptions:
    duplicate_hash: int = 0
    metadata_out_of_bounds: int = 0
    invalid_metadata: int = 0
    payload_out_of_bounds: int = 0
    payload_overlaps_table: int = 0
    invalid_zlib_header: int = 0
    unknown_chunk: int = 0
    suspicious_compression_ratio: int = 0
    random_valid: int = 0
    decoy_metadata_descriptor: bool = False
    incorrect_eocd: bool = False
    false_entries: int = 0

    def total(self) -> int:
        return sum(value for value in vars(self).values()
                   if isinstance(value, int) and not isinstance(value, bool))

    def active(self) -> dict[str, int]:
        return {name: count for name, count in vars(self).items()
                if isinstance(count, int) and not isinstance(count, bool) and count}


JUNK_CLI_OPTIONS = {
    "--junk-duplicate-hash": "duplicate_hash",
    "--junk-metadata-out-of-bounds": "metadata_out_of_bounds",
    "--junk-invalid-metadata": "invalid_metadata",
    "--junk-payload-out-of-bounds": "payload_out_of_bounds",
    "--junk-payload-overlaps-table": "payload_overlaps_table",
    "--junk-invalid-zlib-header": "invalid_zlib_header",
    "--junk-unknown-chunk": "unknown_chunk",
    "--junk-suspicious-compression": "suspicious_compression_ratio",
    "--junk-random-valid": "random_valid",
    "--junk-false-entries": "false_entries",
}

JUNK_NAMES = {
    "dh": "duplicate_hash", "duplicatehash": "duplicate_hash",
    "moob": "metadata_out_of_bounds", "metadataoutofbounds": "metadata_out_of_bounds",
    "im": "invalid_metadata", "invalidmetadata": "invalid_metadata",
    "poob": "payload_out_of_bounds", "payloadoutofbounds": "payload_out_of_bounds",
    "pot": "payload_overlaps_table", "payloadoverlapstable": "payload_overlaps_table",
    "izh": "invalid_zlib_header", "invalidzlibheader": "invalid_zlib_header",
    "uc": "unknown_chunk", "unknownchunk": "unknown_chunk",
    "scr": "suspicious_compression_ratio", "suspiciouscompressionratio": "suspicious_compression_ratio",
    "rv": "random_valid", "randomvalid": "random_valid",
    "dmd": "decoy_metadata_descriptor",
    "decoymetadatadescriptor": "decoy_metadata_descriptor",
    "eocd": "incorrect_eocd", "ieocd": "incorrect_eocd",
    "incorrecteocd": "incorrect_eocd",
    "fe": "false_entries", "falseentries": "false_entries",
}


def parse_junk_specification(value: str, options: JunkOptions) -> None:
    """Apply ``NAME=N; NAME=N`` entertainment settings to *options*."""
    for fragment in value.split(";"):
        fragment = fragment.strip()
        if not fragment:
            continue
        name, separator, raw_count = fragment.partition("=")
        if not separator or not name.strip() or not raw_count.strip():
            raise ValueError(
                "--junk requires NAME=N pairs separated by semicolons "
                "(for example: POOB=5; IM=3)")
        field = JUNK_NAMES.get(name.strip().casefold())
        if field is None:
            names = "DH, MOOB, IM, POOB, POT, IZH, UC, SCR, RV, DMD, EOCD, FE"
            raise ValueError(f"Unknown --junk kind '{name.strip()}'; use {names}")
        try:
            count = int(raw_count.strip())
        except ValueError as exc:
            raise ValueError(f"--junk value for {name.strip()} must be an integer") from exc
        if field in ("decoy_metadata_descriptor", "incorrect_eocd"):
            if count not in (0, 1):
                raise ValueError(f"{name.strip()} must be 0 or 1")
            setattr(options, field, bool(count))
        else:
            setattr(options, field, count)


def validate_junk_options(options: JunkOptions, file_system: str) -> None:
    if file_system in ("ezip", "aem"):
        if not 0 <= options.false_entries <= 10_000:
            raise ValueError("--junk FalseEntries/FE must be from 0 to 10000")
        hashfs_junk = options.active()
        hashfs_junk.pop("false_entries", None)
        if hashfs_junk or options.decoy_metadata_descriptor:
            names = ", ".join(hashfs_junk) or "DecoyMetadataDescriptor"
            raise ValueError(
                f"--junk {names} is supported only by HashFS v1/v2")
        return
    if options.incorrect_eocd:
        raise ValueError("--junk EOCD/IncorrectEOCD is supported only by eZIP and AEM")
    if options.false_entries:
        raise ValueError("--junk FalseEntries/FE is supported only by eZIP and AEM")
    limits = {
        "duplicate_hash": 2 if file_system == "hashfsv2" else 50_000,
        "metadata_out_of_bounds": 30,
        "invalid_metadata": 30,
        "payload_out_of_bounds": 30,
        "payload_overlaps_table": 30,
        "invalid_zlib_header": 30,
        "unknown_chunk": 100,
        "suspicious_compression_ratio": 50_000,
        "random_valid": 1000,
    }
    for name, limit in limits.items():
        count = getattr(options, name)
        if not 0 <= count <= limit:
            raise ValueError(f"--junk {name} must be from 0 to {limit}")
    if options.duplicate_hash == 1:
        raise ValueError(
            "--junk DH/DuplicateHash must be 0 or at least 2 because one record "
            "cannot duplicate a hash")
    if file_system == "hashfsv1" and options.unknown_chunk:
        raise ValueError("--junk UC is supported only by HashFS v2")
    if file_system == "hashfsv1" and options.decoy_metadata_descriptor:
        raise ValueError(
            "--junk DMD/DecoyMetadataDescriptor is supported only by HashFS v2")


KNOWN_JUNK_HEADERS = (
    b"DDS ", b"SiiNunit\n", b"3nK\x01\x00", b"PK\x03\x04",
    b"TOBJ", b"PMG\x00", b"MAT\x00", b"\x89PNG\r\n\x1a\n",
)
EXTRACTOR_EXTENSIONS = (
    # Only resource formats requested for plausible game-facing decoys.
    ".sii", ".sui", ".pmd", ".pma", ".ppd", ".pmg", ".pmc", ".mat",
    ".tobj", ".dds", ".soundref", ".png", ".jpg", ".bank", ".bank.guids",
    ".txt",
)
ENTERTAINMENT_EXTENSION_MARKER = b"SCSX"


def _random_unique_hash(existing: set[int]) -> int:
    while True:
        value = secrets.randbits(64)
        if value not in existing:
            existing.add(value)
            return value


def _extension_marker(extension: str) -> bytes:
    if not extension:
        return b""
    return (ENTERTAINMENT_EXTENSION_MARKER
            + bytes((EXTRACTOR_EXTENSIONS.index(extension),)))


def _random_decoy_payload(extension: str) -> bytes:
    # Most real game assets are not tiny.  Keep a skewed distribution so even
    # a large RV count remains practical, while allowing occasional assets up
    # to the requested 15 MiB maximum.
    roll = secrets.randbelow(100)
    if roll < 80:
        size = 96 * 1024 + secrets.randbelow(1024 * 1024 - 96 * 1024)
    elif roll < 97:
        size = 1024 * 1024 + secrets.randbelow(5 * 1024 * 1024 - 1024 * 1024)
    else:
        size = 5 * 1024 * 1024 + secrets.randbelow(15 * 1024 * 1024 - 5 * 1024 * 1024)
    # Use a real, format-appropriate prefix when available.  The marker is
    # deliberately put after the meaningful header/data, so the extractor can
    # still name an otherwise pathless dumped file without confusing a format
    # sniffer.
    known_prefixes = {
        ".sii": b"SiiNunit\n{\n# ",
        ".dds": (b"DDS " + struct.pack(
            "<IIIIIII11I8I5I", 124, 0x0002100F, 4, 4, 16, 0, 1,
            *([0] * 11), 32, 0x41, 0, 32,
            0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000,
            0x1000, 0, 0, 0, 0) + bytes(64)),
        ".png": (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                 b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"),
    }
    header = known_prefixes.get(extension, b"") if secrets.randbelow(3) == 0 else b""
    if extension == ".dds" and header:
        # DDS reserves this part of its header.  Put the name marker there so
        # it remains inside the extractor's inexpensive first-64-byte probe,
        # while keeping the formal DDS header and pixel payload valid.
        marker = _extension_marker(extension)
        header = header[:32] + marker + header[32 + len(marker):]
    prefix = header + _extension_marker(extension)
    if extension == ".sii" and header:
        prefix += b"\n}\n"
    return prefix + secrets.token_bytes(max(0, size - len(prefix)))


def add_junk_records(records: list[Record] | list[V2Record], options: JunkOptions,
                     file_system: str, salt: int = 0) -> dict[str, int]:
    """Append deliberately malformed and random valid HashFS records."""
    if not options.total():
        return {}
    if file_system not in ("hashfsv1", "hashfsv2"):
        raise ValueError("Entertainment junk records are supported only by HashFS")
    existing_hashes = {record.hash for record in records}
    generated: dict[str, int] = {}
    extension_order = list(EXTRACTOR_EXTENSIONS)
    secrets.SystemRandom().shuffle(extension_order)
    extension_index = 0

    def next_extension() -> str:
        nonlocal extension_index
        extension = extension_order[extension_index % len(extension_order)]
        extension_index += 1
        return extension

    def append(kind: str, payload: bytes, original_size: int | None = None,
               compressed: bool = False, hash_value: int | None = None,
               extension: str | None = None) -> None:
        value = (_random_unique_hash(existing_hashes)
                 if hash_value is None else hash_value)
        extension = extension if extension is not None else next_extension()
        path = f".junk/{kind.casefold()}/{secrets.token_hex(12)}{extension}"
        size = len(payload) if original_size is None else original_size
        if file_system == "hashfsv2":
            records.append(V2Record(path, value, payload, size, compressed,
                                    junk_kind=kind))
        else:
            flags = 2 if compressed else 0
            if kind == "MetadataOutOfBounds":
                flags |= 0x40000000
            records.append(Record(path, value, payload, flags,
                                  zlib.crc32(payload), size, junk_kind=kind))
        generated[kind] = generated.get(kind, 0) + 1

    duplicate_count = options.duplicate_hash
    if duplicate_count:
        duplicate_hash = _random_unique_hash(existing_hashes)
        for _ in range(duplicate_count):
            append("DuplicateHash", secrets.token_bytes(32), hash_value=duplicate_hash)

    ordinary = (
        ("MetadataOutOfBounds", options.metadata_out_of_bounds),
        ("InvalidMetadata", options.invalid_metadata),
        ("PayloadOutOfBounds", options.payload_out_of_bounds),
        ("PayloadOverlapsTable", options.payload_overlaps_table),
    )
    for kind, count in ordinary:
        for _ in range(count):
            append(kind, secrets.token_bytes(32))

    for _ in range(options.unknown_chunk):
        # Only v2 has typed metadata chunks.  The reader recognizes neither
        # kind 7 nor its payload and reports it as an unimplemented entry.
        append("UnknownChunk", _random_decoy_payload(next_extension()))

    for _ in range(options.invalid_zlib_header):
        payload = b"\x00\x00" + secrets.token_bytes(62)
        append("InvalidZlibHeader", payload, 4096, True)

    suspicious_cache: dict[tuple[bytes, str], tuple[bytes, int]] = {}
    for _ in range(options.suspicious_compression_ratio):
        header = secrets.choice(KNOWN_JUNK_HEADERS)
        extension = next_extension()
        cache_key = (header, extension)
        cached = suspicious_cache.get(cache_key)
        if cached is None:
            size = 128 * 1024
            prefix = header + _extension_marker(extension)
            raw = prefix + bytes(size - len(prefix))
            cached = (zlib.compress(raw, 9), size)
            suspicious_cache[cache_key] = cached
        append("SuspiciousCompressionRatio", cached[0], cached[1], True,
               extension=extension)

    for index in range(options.random_valid):
        # Exactly 20% are intentionally unnamed, rather than relying on a
        # random probability that could exceed the requested 30% ceiling in
        # a small sample.
        extension = "" if index % 5 == 0 else next_extension()
        append("RandomValid", _random_decoy_payload(extension), extension=extension)
    return generated


def _surface_layout(width: int, height: int, fmt: int) -> tuple[int, int, int]:
    if fmt in (70, 71, 72, 79, 80, 81):
        row = max(1, (width + 3) // 4) * 8
        rows = max(1, (height + 3) // 4)
        return row, rows, row * rows
    if fmt in (*range(73, 79), *range(82, 85), *range(94, 100)):
        row = max(1, (width + 3) // 4) * 16
        rows = max(1, (height + 3) // 4)
        return row, rows, row * rows
    if fmt in (68, 69, 107):
        row = ((width + 1) >> 1) * 4
        return row, height, row * height
    if fmt in (108, 109):
        row = ((width + 1) >> 1) * 8
        return row, height, row * height
    if fmt in (103, 106):
        row = ((width + 1) >> 1) * 2
        size = row * height + ((row * height + 1) >> 1)
        return row, height + ((height + 1) >> 1), size
    if fmt in (104, 105):
        row = ((width + 1) >> 1) * 4
        size = row * height + ((row * height + 1) >> 1)
        return row, height + ((height + 1) >> 1), size
    if fmt == 110:
        row = ((width + 3) >> 2) * 4
        return row, height * 2, row * height * 2
    bits_per_pixel = {
        **{value: 128 for value in range(1, 5)},
        **{value: 96 for value in range(5, 9)},
        **{value: 64 for value in (*range(9, 15), *range(15, 19), *range(19, 23),
                                  102, 108, 109)},
        **{value: 32 for value in (*range(23, 27), *range(27, 33), *range(33, 39),
                                  *range(39, 48), 67, 68, 69, 87, 88, 89, 90, 91,
                                  92, 93, 100, 101, 107)},
        **{value: 24 for value in (104, 105)},
        **{value: 16 for value in (*range(48, 53), *range(53, 60), 85, 86, 114,
                                  115, 191)},
        **{value: 12 for value in (103, 106, 110)},
        **{value: 8 for value in (*range(60, 66), 65, 111, 112, 113)},
        66: 1,
    }.get(fmt, 0)
    if not bits_per_pixel:
        raise ValueError(f"Unsupported DXGI texture format {fmt}")
    row = (width * bits_per_pixel + 7) // 8
    return row, height, row * height


def _parse_dx10_dds(data: bytes) -> tuple[int, int, int, int, bool, bytes]:
    if len(data) < 148 or data[:4] != b"DDS " or struct.unpack_from("<I", data, 4)[0] != 124:
        raise ValueError("Not a valid DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    mip_count = struct.unpack_from("<I", data, 28)[0] or 1
    if data[84:88] != b"DX10":
        raise ValueError("DDS is not in DX10 format")
    fmt, dimension, misc, array_size, _misc2 = struct.unpack_from("<5I", data, 128)
    if dimension != 3:
        raise ValueError("Only 2D DX10 DDS textures are supported")
    caps2 = struct.unpack_from("<I", data, 112)[0]
    cube = bool(caps2 & 0x200 or misc & 4)
    if array_size not in (0, 1):
        raise ValueError("DDS texture arrays are not supported")
    return width, height, mip_count, fmt, cube, data[148:]


def _pack_dds_surface(data: bytes) -> tuple[tuple[int, int, int, int], bytes]:
    width, height, mip_count, fmt, cube, source = _parse_dx10_dds(data)
    if width < 1 or height < 1 or width > 65536 or height > 65536:
        raise ValueError("DDS dimensions must be between 1 and 65536")
    if not 1 <= mip_count <= 16:
        raise ValueError("HashFS v2 supports between 1 and 16 DDS mip levels")
    if not 1 <= fmt <= 255:
        raise ValueError(f"DXGI texture format {fmt} cannot be represented in HashFS v2")
    output = bytearray()
    source_offset = 0
    faces = 6 if cube else 1
    for _face in range(faces):
        mip_width, mip_height = width, height
        for _mip in range(mip_count):
            output.extend(bytes((-len(output)) % 512))
            row_size, row_count, slice_size = _surface_layout(mip_width, mip_height, fmt)
            consumed = 0
            for _row in range(row_count):
                output.extend(bytes((-len(output)) % 256))
                current_row_size = min(row_size, slice_size - consumed)
                end = source_offset + current_row_size
                if end > len(source):
                    raise ValueError("DDS surface is truncated")
                output.extend(source[source_offset:end])
                source_offset = end
                consumed += current_row_size
            mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
    if source_offset != len(source):
        raise ValueError("DDS contains unsupported trailing surface data")
    image_flags = ((mip_count - 1) | (fmt << 4) | ((1 if cube else 0) << 12)
                   | ((faces - 1) << 14) | (8 << 20) | (9 << 24))
    return (width, height, image_flags, 0), bytes(output)


class TobjWithoutDdsReference(ValueError):
    """A valid TOBJ which intentionally has no DDS texture payload."""


def _tobj_texture_path(tobj: bytes, tobj_path: str) -> tuple[str, int]:
    if len(tobj) < 48 or struct.unpack_from("<I", tobj, 0)[0] != 0x70B10A01:
        raise ValueError("Unsupported TOBJ format")
    length = struct.unpack_from("<Q", tobj, 40)[0]
    if length > len(tobj) - 48:
        raise ValueError("Truncated TOBJ texture path")
    texture = tobj[48:48 + length].decode("utf-8").replace("\\", "/")
    if not texture.startswith("/"):
        parent = tobj_path.rpartition("/")[0]
        texture = posixpath.normpath(posixpath.join(parent, texture))
    texture = normalize_path(texture)
    if not texture.lower().endswith(".dds"):
        raise TobjWithoutDdsReference("TOBJ does not reference a DDS file")
    mag = 1 if tobj[26] in (1, 3) else 0
    minimum = 1 if tobj[27] in (1, 3) else 0
    mip_filter = 1 if tobj[28] == 3 else tobj[28]
    sample_flags = (mag | (minimum << 1) | (mip_filter << 2)
                    | (tobj[30] << 4) | (tobj[31] << 7) | (tobj[32] << 10))
    return texture, sample_flags


def _path_distance(left_path: str, right_path: str) -> int:
    left = posixpath.dirname(left_path).lower().split("/")
    right = posixpath.dirname(right_path).lower().split("/")
    shared = 0
    for left_part, right_part in zip(left, right):
        if left_part != right_part:
            break
        shared += 1
    return len(left) + len(right) - 2 * shared


def add_texture_dependencies(group_sources: dict[str, Path],
                             all_sources: dict[str, Path],
                             reference_root: Path | None = None
                             ) -> tuple[dict[str, Path], int, int, list[tuple[str, str]]]:
    """Add DDS files required by TOBJs in one output group.

    Dependencies are resolved against every ``-i`` source and then, when
    supplied, the ``-ref`` folder. They are added under the exact path
    referenced by the TOBJ. This matters for HashFS v2 with a non-zero salt:
    a mod TOBJ which used to borrow a base-game DDS must carry that DDS when
    converted into its self-contained GDeflate texture entry.
    """
    result = dict(group_sources)
    group_lower = {path.lower(): path for path in result}
    all_lower = {path.lower(): path for path in all_sources}
    dds_by_name: dict[str, list[str]] = {}
    for path in all_sources:
        if path.lower().endswith(".dds"):
            dds_by_name.setdefault(posixpath.basename(path).lower(), []).append(path)
    digest_cache: dict[Path, bytes] = {}

    def digest(source: Path) -> bytes:
        value = digest_cache.get(source)
        if value is None:
            value = hashlib.sha256(source.read_bytes()).digest()
            digest_cache[source] = value
        return value

    tobj_sources = [(path, source) for path, source in group_sources.items()
                    if path.lower().endswith(".tobj")]

    def read_reference(job: tuple[str, Path]) -> tuple[str, str | None]:
        tobj_path, tobj_source = job
        try:
            return tobj_path, _tobj_texture_path(tobj_source.read_bytes(), tobj_path)[0]
        except (OSError, UnicodeError, ValueError):
            return tobj_path, None

    # A dependency/safety scan can touch thousands of small TOBJ files spread
    # across several inputs.  Their reads are independent, so use the same
    # automatic bounded parallelism as the later compression stage.
    workers = min(configured_workers(min(32, max(1, os.cpu_count() or 1))),
                  max(1, len(tobj_sources)))
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="tobj-dependency-scan") as executor:
        references = list(executor.map(read_reference, tobj_sources))

    added = 0
    added_from_reference = 0
    unresolved: list[tuple[str, str]] = []
    for tobj_path, referenced in references:
        if referenced is None:
            continue
        if referenced.lower() in group_lower:
            continue

        selected = all_lower.get(referenced.lower())
        selected_path: Path | None = all_sources[selected] if selected is not None else None
        from_reference = False
        if selected is None:
            candidates = dds_by_name.get(posixpath.basename(referenced).lower(), [])
            if candidates:
                groups: dict[bytes, list[str]] = {}
                for candidate in candidates:
                    groups.setdefault(digest(all_sources[candidate]), []).append(candidate)
                best_group = min(
                    groups.values(),
                    key=lambda paths: (-len(paths),
                                       min(_path_distance(tobj_path, path) for path in paths),
                                       min(path.lower() for path in paths)))
                selected = min(
                    best_group,
                    key=lambda path: (_path_distance(tobj_path, path), path.lower()))
                selected_path = all_sources[selected]
            else:
                # Do an exact, on-demand lookup in -ref.  Building an index
                # over a whole extracted game would be needlessly slow here;
                # the TOBJ already gives us the authoritative virtual path.
                candidate = (reference_root / Path(*referenced.split("/"))
                             if reference_root is not None else None)
                if candidate is None or not candidate.is_file():
                    unresolved.append((tobj_path, referenced))
                    continue
                selected_path = candidate
                from_reference = True

        destination_path = referenced
        if destination_path.lower() not in group_lower:
            if selected_path is None:
                continue
            result[destination_path] = selected_path
            group_lower[destination_path.lower()] = destination_path
            added += 1
            if from_reference:
                added_from_reference += 1
    return result, added, added_from_reference, unresolved


def collect_source_map(sources: dict[str, Path], directory_source: Path,
                       excluded: set[str], hidden: set[str],
                       visible: set[str] | None = None,
                       pruned_directories: set[str] | None = None,
                       explicit_directories: set[str] | None = None) -> list[Item]:
    """Build a virtual HashFS tree from an already resolved source map."""
    visible = visible or set()
    pruned_directories = pruned_directories or set()
    explicit_directories = {
        normalize_path(path) for path in (explicit_directories or set())
        if path and not is_selected(normalize_path(path), excluded)
    }
    sources = {path: source for path, source in sources.items()
               if not is_selected(path, excluded)}
    directories: set[str] = {""}
    for path in sources:
        parts = path.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    for path in explicit_directories:
        parts = path.split("/")
        directories.update("/".join(parts[:index])
                           for index in range(1, len(parts) + 1))

    children: dict[str, list[tuple[str, str, bool]]] = {
        directory: [] for directory in directories}
    for directory in directories:
        if not directory:
            continue
        parent, _, name = directory.rpartition("/")
        children.setdefault(parent, []).append((name, directory, True))
    for path in sources:
        parent, _, name = path.rpartition("/")
        children.setdefault(parent, []).append((name, path, False))

    visibility_cache: dict[str, bool] = {}

    def has_visible_descendant(directory: str) -> bool:
        cached = visibility_cache.get(directory)
        if cached is not None:
            return cached
        for _name, child_path, child_is_directory in children.get(directory, ()):
            if is_hidden(child_path, hidden, visible):
                continue
            if not child_is_directory or has_visible_descendant(child_path):
                visibility_cache[directory] = True
                return True
        visibility_cache[directory] = False
        return False

    queue = deque([""])
    result: list[Item] = []
    while queue:
        directory = queue.popleft()
        names: list[str] = []
        for name, path, is_dir in sorted(children.get(directory, ()), key=lambda row: row[0]):
            if is_dir:
                queue.append(path)
            else:
                result.append(Item(path, sources[path], False))
            if (path not in pruned_directories and not is_hidden(path, hidden, visible)
                    and (not is_dir or has_visible_descendant(path))):
                names.append(("*" if is_dir else "") + name)
        listing = ("\n".join(names) + "\n").encode("utf-8") if names else b""
        preserve_empty = (not names and (
            directory in explicit_directories
            or (directory in PRESERVED_DEFINITION_LISTINGS
                and directory in visible)
        ))
        result.append(Item(directory, directory_source, True, listing,
                           preserve_empty))
    return result


def collect_sources(roots: list[Path], excluded: set[str], hidden: set[str],
                    visible: set[str] | None = None,
                    pruned_directories: set[str] | None = None,
                    explicit_directories: set[str] | None = None) -> list[Item]:
    """Build one virtual HashFS tree from ordered folder/ZIP overlays."""
    sources = {path: source for path, source in overlay_file_sources(roots).items()
               if not is_selected(path, excluded)}
    return collect_source_map(sources, roots[-1], excluded, hidden, visible,
                              pruned_directories, explicit_directories)


def collect(root: Path, excluded: set[str], hidden: set[str], visible: set[str] | None = None,
            pruned_directories: set[str] | None = None,
            explicit_directories: set[str] | None = None) -> list[Item]:
    """Backward-compatible single-source wrapper."""
    return collect_sources([root], excluded, hidden, visible, pruned_directories,
                           explicit_directories)


def records_from_items(items: list[Item], encrypted: set[str], hidden: set[str],
                       compression_method: str = "max",
                       include_empty_files: bool = False,
                       visible: set[str] | None = None,
                       pruned_directories: set[str] | None = None,
                       salt: int = 0,
                       reporter: Reporter | None = None) -> list[Record]:
    visible = visible or set()
    pruned_directories = pruned_directories or set()
    compression_level = {"max": 9, "medium": 6, "min": 1}.get(compression_method)

    def prepare(item: Item) -> Record | None:
        if item.directory:
            if ((not item.listing and not item.preserve_empty_directory)
                    or item.path in pruned_directories
                    or (item.path and is_hidden(item.path, hidden, visible))):
                return None
            original = item.listing
            compressed = (zlib.compress(original, compression_level)
                          if compression_level is not None else original)
            use_compression = (compression_level is not None
                               and len(compressed) < len(original))
            payload = compressed if use_compression else original
            flags = 3 if use_compression else 1
        else:
            original = item.source.read_bytes()
            if not original and not include_empty_files:
                return None
            data = original
            if item.path.lower().endswith(".sii") and is_selected(item.path, encrypted):
                data = encode_3nk(data)
            if not data or compression_level is None:
                payload, flags = data, 0
            else:
                payload, flags = zlib.compress(data, compression_level), 2
        hash_value = hash_archive_path(item.path, salt)
        return Record(item.path, hash_value, payload, flags,
                      zlib.crc32(original), len(data) if not item.directory else len(original))

    workers = configured_workers(max(1, min(32, os.cpu_count() or 1)))
    if reporter is not None:
        reporter.message(f"Parallel preparation: {workers} worker(s).")
    records: list[Record] = []
    seen_hashes: set[int] = set()
    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="hashfs-pack") as executor:
            prepared = executor.map(prepare, items)
            for index, record in enumerate(prepared, 1):
                if reporter is not None:
                    reporter.progress(
                        "Preparing HashFS entries", index, len(items),
                        items[index - 1].path or "/")
                if record is None:
                    continue
                hash_value = record.hash
                item_path = record.path
                if hash_value in seen_hashes:
                    raise ValueError(f"Hash collision or duplicate path: {item_path}")
                seen_hashes.add(hash_value)
                records.append(record)
    finally:
        if reporter is not None:
            reporter.finish_progress()
    return records


def _v2_directory_listing(v1_listing: bytes, directory: str,
                          combined_dds: set[str]) -> bytes:
    names: list[bytes] = []
    for line in v1_listing.decode("utf-8").splitlines():
        if not line:
            continue
        if not line.startswith("*") and line.lower().endswith(".dds"):
            child = normalize_path(posixpath.join(directory, line))
            if child in combined_dds:
                continue
        value = (("/" + line[1:]) if line.startswith("*") else line).encode("utf-8")
        if len(value) > 255:
            raise ValueError(f"HashFS v2 directory item exceeds 255 UTF-8 bytes: {line}")
        names.append(value)
    return struct.pack("<I", len(names)) + bytes(len(name) for name in names) + b"".join(names)


def records_from_items_v2(items: list[Item], encrypted: set[str],
                          compression_method: str, include_empty_files: bool,
                          visible: set[str] | None = None,
                          pruned_directories: set[str] | None = None,
                          hidden: set[str] | None = None,
                          salt: int = 0,
                          gdeflate_enabled: bool = True,
                          reporter: Reporter | None = None) -> list[V2Record]:
    """Prepare HashFS v2 files, typed metadata inputs and binary listings."""
    visible = visible or set()
    pruned_directories = pruned_directories or set()
    hidden = hidden or set()
    compression_level = {"max": 9, "medium": 6, "min": 1}.get(compression_method)
    for item in items:
        for component in item.path.split("/"):
            if len(component.encode("utf-8")) > 255:
                raise ValueError(
                    f"HashFS v2 path component exceeds 255 UTF-8 bytes: {item.path}")
    files = {item.path: item for item in items if not item.directory}
    workers = configured_workers(max(1, min(32, os.cpu_count() or 1)))
    converted_dds: dict[str, bytes] = {}
    combined_records: dict[str, V2Record] = {}
    combined_dds: set[str] = set()
    texture_fallbacks: list[tuple[str, str]] = []
    standalone_tobjs = 0

    dds_items = [item for item in items
                 if not item.directory and item.path.lower().endswith(".dds")]

    def prepare_dds(item: Item) -> tuple[str, bytes, bool]:
        try:
            original = item.source.read_bytes()
            converted = convert_to_dx10(original)
            return item.path, converted, not is_dx10(original)
        except (DDSConversionError, OSError) as exc:
            raise ValueError(f"Cannot convert DDS /{item.path} to DX10: {exc}") from exc

    converted_count = 0
    if dds_items:
        if reporter is not None:
            reporter.message(f"Validating and converting {len(dds_items)} DDS file(s)...")
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="dds-convert") as executor:
            for index, (path, data, converted) in enumerate(
                    executor.map(prepare_dds, dds_items), 1):
                converted_dds[path] = data
                converted_count += int(converted)
                if reporter is not None:
                    reporter.progress(
                        "Converting DDS to DX10", index, len(dds_items), path)
        if reporter is not None:
            reporter.finish_progress()
        if reporter is not None:
            reporter.message(
                f"DDS conversion: {converted_count} legacy file(s) converted to DX10; "
                f"{len(dds_items) - converted_count} already DX10.")

    # A surprising number of otherwise valid mods move a DDS without updating
    # the absolute path stored in its TOBJ.  Build indexes over the complete
    # merged input, rather than limiting pairing to the TOBJ's source folder.
    dds_by_lower_path = {path.lower(): path for path in converted_dds}
    dds_by_name: dict[str, list[str]] = {}
    for path in converted_dds:
        dds_by_name.setdefault(posixpath.basename(path).lower(), []).append(path)

    def resolve_dds_path(referenced_path: str, tobj_path: str) -> str:
        if referenced_path in converted_dds:
            return referenced_path
        case_insensitive = dds_by_lower_path.get(referenced_path.lower())
        if case_insensitive is not None:
            return case_insensitive
        candidates = dds_by_name.get(posixpath.basename(referenced_path).lower(), [])
        if not candidates:
            raise KeyError(referenced_path)

        target_parts = posixpath.dirname(tobj_path).lower().split("/")

        def distance(path: str) -> int:
            parts = posixpath.dirname(path).lower().split("/")
            shared = 0
            for left, right in zip(target_parts, parts):
                if left != right:
                    break
                shared += 1
            return len(target_parts) + len(parts) - 2 * shared

        # Prefer the most common byte-identical texture.  This handles mods
        # which duplicate one shared texture in several model folders.  For a
        # tie, use the path closest to the TOBJ and finally lexical order so
        # builds remain deterministic.
        groups: dict[bytes, list[str]] = {}
        for path in candidates:
            digest = hashlib.sha256(converted_dds[path]).digest()
            groups.setdefault(digest, []).append(path)
        best_group = min(
            groups.values(),
            key=lambda group: (-len(group), min(distance(path) for path in group),
                               min(path.lower() for path in group)))
        return min(best_group, key=lambda path: (distance(path), path.lower()))

    if gdeflate_enabled:
        tobj_items = [item for item in items
                      if not item.directory and item.path.lower().endswith(".tobj")]

        def prepare_texture(item: Item) -> tuple[str, str | None, V2Record | None, str | None]:
            dds_path: str | None = None
            try:
                original = item.source.read_bytes()
                referenced_path, sample_flags = _tobj_texture_path(original, item.path)
                dds_path = resolve_dds_path(referenced_path, item.path)
                texture_meta, surface = _pack_dds_surface(converted_dds[dds_path])
                # The texture-record type remains mandatory, but -cm controls
                # its surface compression as well.  GDeflate.compress returns
                # the unchanged surface for ``no`` without loading the codec.
                compressed = compress_gdeflate(surface, compression_method)
                use_gdeflate = len(compressed) < len(surface)
                payload = compressed if use_gdeflate else surface
                width, height, image_flags, _unused = texture_meta
                record = V2Record(
                    item.path, hash_archive_path(item.path, salt), payload,
                    len(surface), use_gdeflate,
                    texture_meta=(width, height, image_flags, sample_flags))
                return item.path, dds_path, record, None
            except TobjWithoutDdsReference:
                # Valid standalone TOBJ (for example an intentionally empty
                # texture slot): store it as an ordinary file without noise.
                return item.path, None, None, None
            except (GDeflateError, KeyError, OSError, UnicodeError, ValueError) as exc:
                return item.path, dds_path, None, str(exc)

        if reporter is not None and tobj_items:
            reporter.message(
                f"Analyzing {len(tobj_items)} TOBJ/DDS pair(s) for GDeflate...")
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="gdeflate-pack") as executor:
            for index, (tobj_path, dds_path, record, error) in enumerate(
                    executor.map(prepare_texture, tobj_items), 1):
                if record is not None and dds_path is not None:
                    combined_records[tobj_path] = record
                    combined_dds.add(dds_path)
                elif error is not None:
                    texture_fallbacks.append((tobj_path, error))
                else:
                    standalone_tobjs += 1
                if reporter is not None:
                    reporter.progress(
                        "Encoding GDeflate textures", index, len(tobj_items),
                        tobj_path)
        if reporter is not None:
            reporter.finish_progress()
        if reporter is not None:
            compressed_textures = sum(
                record.compressed for record in combined_records.values())
            raw_textures = len(combined_records) - compressed_textures
            reporter.message(
                f"GDeflate textures: {len(combined_records)} combined; "
                f"{compressed_textures} compressed; {raw_textures} stored raw; "
                f"{standalone_tobjs} standalone TOBJ; "
                f"{len(texture_fallbacks)} pair(s) stored separately.")
            if reporter.level >= 2:
                for path, reason in texture_fallbacks:
                    reporter.message(f"GDEFLATE FALLBACK /{path}: {reason}")

    def prepare(item: Item) -> V2Record | None:
        if item.directory:
            if ((not item.listing and not item.preserve_empty_directory)
                    or item.path in pruned_directories
                    or (item.path and is_hidden(item.path, hidden, visible))):
                return None
            original = _v2_directory_listing(item.listing, item.path, combined_dds)
            compressed = zlib.compress(original, compression_level) if compression_level else original
            use_compression = compression_level is not None and len(compressed) < len(original)
            payload = compressed if use_compression else original
            return V2Record(item.path, hash_archive_path(item.path, salt), payload,
                            len(original), use_compression, directory=True)

        if item.path in combined_dds:
            return None
        if item.path in combined_records:
            return combined_records[item.path]
        original = (converted_dds[item.path]
                    if item.path.lower().endswith(".dds")
                    else item.source.read_bytes())
        if not original and not include_empty_files:
            return None
        data = original
        if item.path.lower().endswith(".sii") and is_selected(item.path, encrypted):
            data = encode_3nk(data)
        compressed = zlib.compress(data, compression_level) if compression_level else data
        use_compression = compression_level is not None and len(compressed) < len(data)
        payload = compressed if use_compression else data
        return V2Record(item.path, hash_archive_path(item.path, salt), payload, len(data),
                        use_compression, pmg=item.path.lower().endswith(".pmg"))

    if reporter is not None:
        reporter.message(f"Parallel HashFS v2 preparation: {workers} worker(s).")
    records: list[V2Record] = []
    seen_hashes: set[int] = set()
    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="hashfs2-pack") as executor:
            for index, record in enumerate(executor.map(prepare, items), 1):
                if reporter is not None:
                    reporter.progress(
                        "Preparing HashFS v2 entries", index, len(items),
                        items[index - 1].path or "/")
                if record is None:
                    continue
                if record.hash in seen_hashes:
                    raise ValueError(f"Hash collision or duplicate path: {record.path}")
                seen_hashes.add(record.hash)
                records.append(record)
    finally:
        if reporter is not None:
            reporter.finish_progress()
    return records


def _v2_metadata(records: list[V2Record], entry_start: int = 0,
                 metadata_start: int = 0,
                 archive_size: int = 0) -> tuple[bytes, list[tuple[int, int, int, int]]]:
    def descriptor(index: int, kind: int) -> bytes:
        if index > 0xFFFFFF:
            raise ValueError("HashFS v2 metadata index exceeds the 24-bit limit")
        return bytes((index & 255, (index >> 8) & 255, (index >> 16) & 255, kind))

    metadata = bytearray()
    rows: list[tuple[int, int, int, int]] = []
    for record in records:
        declared_stored_size = len(record.payload)
        declared_original_size = record.original_size
        if declared_stored_size > 0x0FFFFFFF or declared_original_size > 0x0FFFFFFF:
            raise ValueError(f"HashFS v2 entry exceeds 256 MiB metadata limit: {record.path}")
        meta_index = len(metadata) // 4
        if record.junk_kind == "UnknownChunk":
            # A descriptor whose type is deliberately outside the implemented
            # file/directory/texture kinds.  Its body index is valid but has
            # no semantic payload; HashFS readers should skip it gracefully.
            metadata += descriptor(meta_index, 7)
            rows.append((record.hash, meta_index, 1, record.flags))
            continue
        decoy_count = 1 if record.decoy_descriptor else 0
        if record.texture_meta is not None:
            width, height, image_flags, sample_flags = record.texture_meta
            if not (1 <= width <= 65536 and 1 <= height <= 65536):
                raise ValueError(f"HashFS v2 texture dimensions are out of range: {record.path}")
            body_index = meta_index + 3 + decoy_count
            if decoy_count:
                metadata += descriptor(body_index, 7)
            metadata += descriptor(body_index, 1)
            metadata += descriptor(body_index + 2, 2)
            metadata += descriptor(body_index + 3, 132)
            metadata += struct.pack("<HHII", width - 1, height - 1,
                                    image_flags, sample_flags)
            compressed_field = (declared_stored_size
                                | (0x30000000 if record.compressed else 0))
            metadata += struct.pack("<IIII", compressed_field,
                                    declared_original_size | 0x30000000,
                                    0, record.offset // 16)
            rows.append((record.hash, meta_index, 3 + decoy_count, 0))
        else:
            count = (2 if record.pmg else 1) + decoy_count
            body_index = meta_index + count
            kind = 129 if record.directory else 128
            if decoy_count:
                metadata += descriptor(body_index, 7)
            metadata += descriptor(body_index, kind)
            if record.pmg:
                unknown_index = body_index + 4
                metadata += descriptor(unknown_index, 6)
            compressed_field = (declared_stored_size
                                | (0x10000000 if record.compressed else 0))
            block = record.offset // 16
            if record.junk_kind == "PayloadOutOfBounds":
                block = ((max(archive_size, record.offset + len(record.payload))
                          + 0x100000) + 15) // 16
            elif record.junk_kind == "PayloadOverlapsTable":
                block = entry_start // 16
            metadata += struct.pack("<IIII", compressed_field, declared_original_size,
                                    0, block)
            if record.pmg:
                metadata += bytes(8)
            if record.junk_kind == "MetadataOutOfBounds":
                rows.append((record.hash, 0xFFFFFF, count, record.flags))
            elif record.junk_kind == "InvalidMetadata":
                rows.append((record.hash, meta_index, 0, record.flags))
            else:
                rows.append((record.hash, meta_index, count, record.flags))
    if len(metadata) // 4 > 0xFFFFFFFF:
        raise ValueError("HashFS v2 metadata table is too large")
    return bytes(metadata), rows


def normalize_v2_table_mode(value: str) -> str:
    """Normalize the order-independent HashFS v2 table-mode grammar."""
    compact = value.casefold().replace("_", "").replace("-", "")
    if compact in ("r", "random"):
        return "random"

    locations: dict[str, int] = {}
    for number, target in re.findall(r"([1-4])([tm])", compact):
        if target in locations:
            raise ValueError(f"HashFS v2 table mode specifies {target} more than once")
        locations[target] = int(number)
    compact = re.sub(r"[1-4][tm]", "", compact)

    compression_aliases = {
        "": "compressed",
        "c": "compressed",
        "compressed": "compressed",
        "u": "uncompressed",
        "uncompressed": "uncompressed",
        "um": "uncompressed_metadata",
        "uncompressedm": "uncompressed_metadata",
        "uncompressedmetadata": "uncompressed_metadata",
        "ut": "uncompressed_table",
        "uncompressedt": "uncompressed_table",
        "uncompressedtable": "uncompressed_table",
        "cm": "compressed_metadata",
        "compressedm": "compressed_metadata",
        "compressedmetadata": "compressed_metadata",
        "ct": "compressed_table",
        "compressedt": "compressed_table",
        "compressedtable": "compressed_table",
    }
    if compact not in compression_aliases:
        raise ValueError(
            "Invalid HashFS v2 table mode; expected positions such as 1t2m "
            "and compression c, u, um, ut, cm or ct")
    table_location = locations.get("t", 2)
    metadata_location = locations.get("m", 2)
    return (f"{table_location}t_{metadata_location}m_"
            f"{compression_aliases[compact]}")


def random_v2_table_mode() -> str:
    # A table at 0x40 can be larger than the space before a table fixed at
    # 0x1000. Avoid that intrinsically conflicting pair in random mode.
    compatible_locations = [
        (table, metadata)
        for table in range(1, 5)
        for metadata in range(1, 5)
        if (table, metadata) not in ((1, 3), (3, 1))
    ]
    table_location, metadata_location = secrets.choice(compatible_locations)
    compression = secrets.choice((
        "compressed", "uncompressed", "compressed_table",
        "compressed_metadata"))
    return f"{table_location}t_{metadata_location}m_{compression}"


def _v2_table_settings(table_mode: str) -> tuple[int, int, bool, bool]:
    normalized = normalize_v2_table_mode(table_mode)
    if normalized == "random":
        raise ValueError("Random HashFS v2 table mode must be resolved before planning")
    match = re.fullmatch(
        r"([1-4])t_([1-4])m_(compressed|uncompressed|"
        r"uncompressed_metadata|uncompressed_table|compressed_metadata|"
        r"compressed_table)",
        normalized)
    if match is None:
        raise ValueError(f"Invalid HashFS v2 table mode: {table_mode}")
    table_location = int(match.group(1))
    metadata_location = int(match.group(2))
    compression = match.group(3)
    compression_modes = {
        "compressed": (True, True),
        "uncompressed": (False, False),
        "uncompressed_metadata": (True, False),
        "uncompressed_table": (False, True),
        "compressed_metadata": (False, True),
        "compressed_table": (True, False),
    }
    compress_entries, compress_metadata = compression_modes[compression]
    return table_location, metadata_location, compress_entries, compress_metadata


def _zlib_compress_bound(size: int) -> int:
    """Return zlib's documented upper bound without requiring its C API."""
    return size + (size >> 12) + (size >> 14) + (size >> 25) + 13


def plan_archive_v2(records: list[V2Record],
                    table_mode: str = "2t_2m_compressed",
                    reporter: Reporter | None = None,
                    ) -> tuple[int, int, int, bytes, bytes]:
    if not records:
        raise ValueError("No entries can be written. Folder is empty")
    (table_location, metadata_location,
     compress_entries, compress_metadata) = _v2_table_settings(table_mode)

    # Entry rows do not contain payload offsets, so their stored representation
    # can be finalized before positioning payloads. Metadata does contain those
    # offsets and is regenerated after the layout has been assigned.
    metadata_probe, rows = _v2_metadata(records)
    entry_raw = b"".join(
        struct.pack("<QIHH", *row) for row in sorted(rows))
    entry_data = (zlib.compress(entry_raw, 9)
                  if compress_entries else entry_raw)

    metadata_capacity = (_zlib_compress_bound(len(metadata_probe))
                         if compress_metadata else len(metadata_probe))
    capacities = {"t": len(entry_data), "m": metadata_capacity}
    locations = {"t": table_location, "m": metadata_location}
    starts: dict[str, int] = {}

    def report_relocation(target: str, offset: int) -> None:
        if reporter is None:
            return
        names = {"t": "Table", "m": "Metadata"}
        other = "m" if target == "t" else "t"
        reporter.message(
            f"WARNING: {names[target]} moved to offset 0x{offset:X} because "
            f"it overlapped the {names[other].lower()}.")

    # Mode 3 means immediately after the longer v2 header, hence 0x40 rather
    # than HashFS v1's unsafe 0x20. Mode 1 remains exactly 0x1000.
    early_cursor = 0x40
    for target in ("t", "m"):
        if locations[target] == 3:
            starts[target] = early_cursor
            if early_cursor != 0x40:
                report_relocation(target, early_cursor)
            early_cursor = (early_cursor + capacities[target] + 15) & ~15
    fixed_cursor = 0x40
    if any(location == 1 for location in locations.values()):
        fixed_cursor = max(0x1000, (early_cursor + 15) & ~15)
        for target in ("t", "m"):
            if locations[target] == 1:
                starts[target] = fixed_cursor
                if fixed_cursor != 0x1000:
                    report_relocation(target, fixed_cursor)
                fixed_cursor = (fixed_cursor + capacities[target] + 15) & ~15

    position = max(0x40, early_cursor, fixed_cursor)
    random_after: dict[str, int] = {}
    for target in ("t", "m"):
        if locations[target] == 4:
            random_after[target] = choose_table_file_index(records)

    for index, record in enumerate(records):
        position = (position + 15) & ~15
        record.offset = position
        position += len(record.payload)
        for target in ("t", "m"):
            if random_after.get(target) == index:
                position = (position + 15) & ~15
                starts[target] = position
                position += capacities[target]

    for target in ("t", "m"):
        if locations[target] == 2:
            position = (position + 15) & ~15
            starts[target] = position
            position += capacities[target]

    entry_start = starts["t"]
    metadata_start = starts["m"]

    metadata, _rows = _v2_metadata(
        records, entry_start, metadata_start, position)
    metadata_data = (zlib.compress(metadata, 9)
                     if compress_metadata else metadata)
    if len(metadata_data) > metadata_capacity:
        raise ValueError("Compressed HashFS v2 metadata exceeded its reserved space")
    archive_size = max(
        entry_start + len(entry_data),
        metadata_start + len(metadata_data),
        *(record.offset + len(record.payload) for record in records))
    if max(entry_start, metadata_start, archive_size) > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("HashFS v2 archive is too large")
    return entry_start, metadata_start, archive_size, entry_data, metadata_data


def write_archive_v2(destination: Path, records: list[V2Record],
                     reporter: Reporter | None = None, salt: int = 0,
                     table_mode: str = "2t_2m_compressed") -> tuple[int, int]:
    entry_start, metadata_start, _size, entry_data, metadata_data = plan_archive_v2(
        records, table_mode, reporter)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".sxcpack2_", suffix=".tmp",
                                         dir=destination.parent, delete=False) as output:
            temp_name = output.name
            output.write(bytes(0x40))
            parts: list[tuple[int, str, bytes, V2Record | None]] = [
                (entry_start, "table", entry_data, None),
                (metadata_start, "metadata", metadata_data, None),
            ]
            parts.extend((record.offset, "payload", record.payload, record)
                         for record in records)
            written_records = 0
            for offset, _kind, data, record in sorted(parts, key=lambda part: part[0]):
                if offset < output.tell():
                    raise ValueError("HashFS v2 table/payload layout overlaps")
                output.write(bytes(offset - output.tell()))
                output.write(data)
                if record is not None:
                    written_records += 1
                    if reporter is not None:
                        reporter.write_entry(
                            record, written_records, len(records), "HashFS v2")
            output.seek(0)
            metadata, _rows = _v2_metadata(
                records, entry_start, metadata_start, _size)
            output.write(struct.pack(
                "<4sHH4sIIIIQQQB", b"SCS#", 2, salt, b"CITY", len(records),
                len(entry_data), len(metadata) // 4, len(metadata_data),
                entry_start, metadata_start, 0, 0))
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        if reporter is not None:
            reporter.finish_progress()
    return entry_start, metadata_start


def choose_table_file_index(records: list[Record]) -> int:
    total_bytes = sum(len(record.payload) for record in records)
    candidates: list[tuple[int, int]] = []
    position = 0
    for index, record in enumerate(records):
        position += len(record.payload)
        if not record.flags & 1:
            candidates.append((index, position))
    if not candidates:
        raise ValueError("Table mode 4 requires at least one file")
    third = (0, 1, 1, 2)[secrets.randbelow(4)]
    thirds: list[list[int]] = [[], [], []]
    for index, end in candidates:
        bucket = 0 if 3 * end < total_bytes else 1 if 3 * end <= 2 * total_bytes else 2
        thirds[bucket].append(index)
    if thirds[third]:
        return secrets.choice(thirds[third])
    target = (1, 3, 5)[third] * total_bytes
    nearest = min(abs(6 * end - target) for _, end in candidates)
    return secrets.choice([index for index, end in candidates
                           if abs(6 * end - target) == nearest])


def plan_archive(records: list[Record], table_mode: int = 1) -> tuple[int, int | None, int]:
    """Assign payload offsets and return table offset, insertion point and final size."""
    if not records:
        raise ValueError("No entries can be written. Folder is empty")
    if table_mode not in (1, 2, 3, 4):
        raise ValueError("Table mode must be 1, 2, 3 or 4")
    table_size = len(records) * 32
    insert_after: int | None = None
    if table_mode in (1, 3):
        table_offset = 4096 if table_mode == 1 else 0x20
        payload_offset = table_offset + table_size
        for record in records:
            record.offset = payload_offset
            payload_offset += len(record.payload)
    else:
        payload_offset = 32
        if table_mode == 4:
            insert_after = choose_table_file_index(records)
        for index, record in enumerate(records):
            record.offset = payload_offset
            payload_offset += len(record.payload)
            if index == insert_after:
                table_offset = (payload_offset + 15) & ~15
                payload_offset = table_offset + table_size
        if table_mode == 2:
            table_offset = (payload_offset + 15) & ~15
            archive_size = table_offset + table_size
        else:
            archive_size = payload_offset + (16 if insert_after == len(records) - 1 else 0)
    if table_mode in (1, 3):
        archive_size = payload_offset
    if table_offset + table_size > 0xFFFFFFFF or payload_offset > 0xFFFFFFFF:
        raise ValueError("HashFS v1 archive is too large")
    return table_offset, insert_after, archive_size


def write_archive(destination: Path, records: list[Record], salt: int = 0,
                  table_mode: int = 1, reporter: Reporter | None = None) -> int:
    table_offset, insert_after, _archive_size = plan_archive(records, table_mode)
    table_size = len(records) * 32
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".sxcpack_", suffix=".tmp",
                                         dir=destination.parent, delete=False) as output:
            temp_name = output.name
            output.write(struct.pack("<4sHH4sII", b"SCS#", 1, salt, b"CITY",
                                     len(records), table_offset))
            def write_table() -> None:
                for record in sorted(records, key=lambda value: value.hash):
                    position = record.offset
                    flags = record.flags
                    original_size = record.original_size
                    stored_size = len(record.payload)
                    if record.break_sizes:
                        original_size += 0x100000
                        stored_size += 0x100000
                    if record.junk_kind == "PayloadOutOfBounds":
                        position = _archive_size + 0x100000
                    elif record.junk_kind == "PayloadOverlapsTable":
                        position = table_offset
                    elif record.junk_kind == "InvalidMetadata":
                        flags |= 2
                        stored_size = 0
                        original_size = max(1, original_size)
                    output.write(struct.pack(
                        "<QQIIII", record.hash, position, flags,
                        record.crc, original_size, stored_size))

            if table_mode in (1, 3):
                output.write(bytes(table_offset - output.tell()))
                write_table()
            else:
                output.write(bytes(32 - output.tell()))
            for index, record in enumerate(records):
                output.write(bytes(record.offset - output.tell()))
                output.write(record.payload)
                if reporter is not None:
                    reporter.write_entry(record, index + 1, len(records))
                if index == insert_after:
                    output.write(bytes(table_offset - output.tell()))
                    write_table()
            if table_mode == 2:
                output.write(bytes(table_offset - output.tell()))
                write_table()
            elif table_mode == 4 and insert_after == len(records) - 1:
                output.write(bytes(16))
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        if reporter is not None:
            reporter.finish_progress()
    return table_offset


def _source_mtime(source: Path) -> float:
    try:
        return source.stat().st_mtime
    except (OSError, ValueError, OverflowError):
        return time.time()


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _shuffle_zip_records_parent_first(records: list[Record]) -> list[Record]:
    """Randomize ZIP entries while emitting explicit parents before children.

    WinRAR creates a virtual, unencrypted directory when it encounters a child
    before that directory's own central entry.  It does not reliably refresh
    the lock marker later.  A randomized tree walk keeps entries interleaved
    without triggering that presentation bug.
    """
    randomizer = secrets.SystemRandom()
    directories = {record.path for record in records if record.flags & 1}
    children: dict[str, list[Record]] = {}
    for record in records:
        parent = posixpath.dirname(record.path)
        while parent and parent not in directories:
            parent = posixpath.dirname(parent)
        children.setdefault(parent, []).append(record)

    shuffled: list[Record] = []

    def append_children(parent: str) -> None:
        siblings = children.get(parent, [])
        randomizer.shuffle(siblings)
        for record in siblings:
            shuffled.append(record)
            if record.flags & 1:
                append_children(record.path)

    append_children("")
    if len(shuffled) != len(records):
        raise ValueError("Cannot arrange ZIP entries into a valid directory tree")
    return shuffled


def zip_records_from_sources(roots: list[Path], include_empty_files: bool,
                             include_directories: bool,
                             sort_entries: bool = False,
                             reporter: Reporter | None = None,
                             file_system: str = "ezip") -> list[Record]:
    """Build overlayed, parallel-Deflated records for Ghostly eZIP and AEM."""
    sources = overlay_file_sources(roots)
    file_paths = sorted(sources, key=str.casefold)

    def read_file(path: str) -> Record | None:
        source = sources[path]
        data = source.read_bytes()
        if not data and not include_empty_files:
            return None
        payload = _raw_deflate(data)
        flags = 0
        # Real AEM writers store data which would grow under Deflate.  Bit 1 is
        # private to the packer (bit 0 remains the directory marker).
        if file_system == "aem" and len(payload) >= len(data):
            payload = data
            flags |= 2
        return Record(path, 0, payload, flags,
                      zlib.crc32(data), len(data),
                      mtime=_source_mtime(source))

    workers = configured_workers(max(1, min(32, os.cpu_count() or 1)))
    if reporter is not None:
        reporter.message(f"Parallel ZIP compression: {workers} worker(s).")
    records: list[Record] = []
    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="zip-pack") as executor:
            for index, record in enumerate(executor.map(read_file, file_paths), 1):
                if reporter is not None:
                    reporter.progress(
                        "Compressing ZIP entries", index, len(file_paths),
                        file_paths[index - 1])
                if record is not None:
                    records.append(record)
    finally:
        if reporter is not None:
            reporter.finish_progress()

    if include_directories:
        directories: dict[str, float] = {}
        for root in roots:
            for candidate in root.rglob("*"):
                if candidate.is_dir():
                    relative = normalize_path(candidate.relative_to(root).as_posix())
                    if relative and relative not in sources:
                        directories[relative] = _source_mtime(candidate)
        directory_records = [
            # A zero-length Stored entry has the encryption bit, but WinRAR
            # treats it as a metadata-only directory and does not display the
            # encrypted marker.  Give it a valid raw-Deflate empty stream so
            # the Ghostly eZIP flag is visible consistently for directories.
            Record(path, 0, (b"" if file_system == "aem" else _raw_deflate(b"")),
                   (3 if file_system == "aem" else 1), 0, 0,
                   mtime=directories[path])
            for path in sorted(directories, key=str.casefold)]
        records = directory_records + records
    if not sort_entries:
        # ZIP itself places no ordering constraint on entries.  Keep explicit
        # parents ahead of descendants solely so archive GUIs display their
        # encryption marker consistently.
        records = _shuffle_zip_records_parent_first(records)
    return records


def _dos_timestamp(timestamp: float | None) -> tuple[int, int]:
    try:
        local = time.localtime(time.time() if timestamp is None else timestamp)
    except (OSError, ValueError, OverflowError):
        local = time.localtime()
    if not 1980 <= local.tm_year <= 2107:
        local = time.localtime()
    year = local.tm_year
    dos_time = (local.tm_hour << 11) | (local.tm_min << 5) | (local.tm_sec // 2)
    dos_date = ((year - 1980) << 9) | (local.tm_mon << 5) | local.tm_mday
    return dos_time, dos_date


def _zip64_extra(uncompressed: int | None = None, compressed: int | None = None,
                 offset: int | None = None) -> bytes:
    body = b"".join(struct.pack("<Q", value)
                    for value in (uncompressed, compressed, offset)
                    if value is not None)
    return struct.pack("<HH", 1, len(body)) + body if body else b""


_FALSE_ZIP_NAMES = (
    "manifest.sii", "def/city.sii", "def/country.sii", "def/company.sii",
    "def/world/model.sii", "def/world/prefab.sii", "description.txt", "icon.jpg",
)


def _false_zip_name_pool(records: list[Record]) -> list[str]:
    paths = [record.path.rstrip("/") + ("/" if record.flags & 1 else "")
             for record in records]
    definitions = [path for path in paths
                   if path.casefold().endswith((".sii", ".sui"))]
    # Common package/definition paths are intentionally weighted so a large
    # decoy table contains repeated believable names rather than only noise.
    return list(_FALSE_ZIP_NAMES) * 8 + definitions * 3 + paths


def _write_false_zip_entries(output, records: list[Record], count: int) -> tuple[int, int]:
    """Write unreferenced central-directory rows and return their byte range."""
    start = output.tell()
    pool = _false_zip_name_pool(records)
    for _ in range(count):
        name_bytes = secrets.choice(pool).encode("utf-8")[:0xFFFF]
        output.write(struct.pack(
            "<4sHHHHHHIIIHHHHHII", b"PK\x01\x02",
            0x031E, 20, 0, 8, 0, 0,
            secrets.randbits(32),
            1 + secrets.randbelow(0xFFFF),
            1 + secrets.randbelow(0xFFFF),
            len(name_bytes), 0, 0, 0, 0,
            0x01A40000, secrets.randbits(31)))
        output.write(name_bytes)
    return start, output.tell()


def _write_false_sfx_preamble(output, records: list[Record], extra_entries: int = 0) -> None:
    """Write a short false RAR/SFX header followed by split ZIP directories."""
    prefix = bytearray(secrets.token_bytes(0x50))
    prefix[:2] = b"MZ"
    prefix[0x40:0x48] = b"Rar!\x1a\x07\x01\x00"
    output.write(prefix)

    decoy_start = output.tell()
    split_at = 50
    _write_false_zip_entries(output, records, split_at)
    second_start = output.tell()
    second_count = 5 + extra_entries
    _write_false_zip_entries(output, records, second_count)

    decoy_end = output.tell()
    output.write(struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, second_count, second_count,
        decoy_end - second_start, second_start, 0))
    zip64_start = output.tell()
    output.write(struct.pack(
        "<4sQHHIIQQQQ", b"PK\x06\x06", 44, 0x031E, 45, 0, 0,
        split_at, split_at, second_start - decoy_start, decoy_start))
    output.write(struct.pack("<4sIQI", b"PK\x06\x07", 0, zip64_start, 1))
    output.write(struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0,
        0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))


def write_disguised_zip(destination: Path, records: list[Record], file_system: str,
                        reporter: Reporter | None = None,
                        incorrect_eocd: bool = False,
                        false_entries: int = 0) -> None:
    """Assemble precompressed Ghostly eZIP or AEM records atomically."""
    if file_system not in ("ezip", "aem"):
        raise ValueError(f"Unsupported disguised ZIP format: {file_system}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".scszip_", suffix=".tmp",
                                         dir=destination.parent, delete=False) as temporary:
            temp_name = temporary.name
        central_rows: list[tuple[Record, bytes, int, int, int, int]] = []
        with Path(temp_name).open("wb") as output:
            protected_ezip = incorrect_eocd and file_system == "ezip"
            initial_false_entries = ((false_entries + 2) // 3
                                     if false_entries else 0)
            scattered_false_entries = false_entries - initial_false_entries
            false_after_entry = [0] * len(records)
            randomizer = secrets.SystemRandom()
            entry_slots = list(range(len(records)))
            randomizer.shuffle(entry_slots)
            for false_index in range(scattered_false_entries):
                if false_index and false_index % len(entry_slots) == 0:
                    randomizer.shuffle(entry_slots)
                false_after_entry[entry_slots[false_index % len(entry_slots)]] += 1
            # AEM must retain AEM! at offset zero.  Its "beginning" group is
            # therefore placed immediately after the first genuine record.
            if file_system == "aem" and initial_false_entries:
                false_after_entry[0] += initial_false_entries
            if protected_ezip:
                _write_false_sfx_preamble(output, records, initial_false_entries)
            elif file_system == "ezip" and initial_false_entries:
                _write_false_zip_entries(output, records, initial_false_entries)
            for index, record in enumerate(records, 1):
                name = record.path.rstrip("/") + ("/" if record.flags & 1 else "")
                name_bytes = name.encode("utf-8")
                if len(name_bytes) > 0xFFFF:
                    raise ValueError(f"ZIP entry name is too long: {name}")
                local_offset = output.tell()
                compressed_size = len(record.payload)
                uncompressed_size = record.original_size
                needs_zip64_size = max(compressed_size, uncompressed_size) >= 0xFFFFFFFF
                dos_time, dos_date = _dos_timestamp(record.mtime)
                if file_system == "aem":
                    # AEM is not merely ZIP with another signature.  Its local
                    # records deliberately discard all metadata; the central
                    # directory is authoritative.  Empty entries replace the
                    # local name-length/name area with another AEM marker and
                    # zero padding, exactly as the reference archives do.
                    flags = 1
                    output.write(b"AEM!" + (b"\0" * 22))
                    if compressed_size:
                        output.write(struct.pack("<I", len(name_bytes)))
                        output.write(name_bytes)
                    else:
                        output.write(b"AEM!")
                        output.write(b"\0" * len(name_bytes))
                elif protected_ezip:
                    # The local record only preserves the two lengths needed
                    # to locate the payload.  Its name and metadata are decoys;
                    # the authoritative codec, sizes and path live in the
                    # final ZIP64 directory.
                    local_extra_length = 544 + secrets.randbelow(1537)
                    flags = 0x2049
                    output.write(struct.pack(
                        "<4sHHHHHIIIHH", b"PK\x03\x04", 99, flags, 0,
                        secrets.randbelow(0x10000), secrets.randbelow(0x10000),
                        secrets.randbits(32), 0, 0,
                        len(name_bytes), local_extra_length))
                    output.write(secrets.token_bytes(len(name_bytes)))
                    output.write(secrets.token_bytes(local_extra_length))
                else:
                    local_extra = (_zip64_extra(uncompressed_size, compressed_size)
                                   if needs_zip64_size else b"")
                    flags = 0x801
                    output.write(struct.pack(
                        "<4sHHHHHIIIHH", b"PK\x03\x04",
                        45 if needs_zip64_size else 20, flags, 8,
                        dos_time, dos_date, record.crc,
                        0xFFFFFFFF if needs_zip64_size else compressed_size,
                        0xFFFFFFFF if needs_zip64_size else uncompressed_size,
                        len(name_bytes), len(local_extra)))
                    output.write(name_bytes)
                    output.write(local_extra)
                output.write(record.payload)
                central_rows.append((record, name_bytes, local_offset,
                                     flags, dos_time, dos_date))
                if false_after_entry[index - 1]:
                    _write_false_zip_entries(
                        output, records, false_after_entry[index - 1])
                if reporter is not None:
                    reporter.write_entry(
                        record, index, len(records),
                        "Ghostly eZIP" if file_system == "ezip" else "AEM")
            if protected_ezip:
                secrets.SystemRandom().shuffle(central_rows)
            central_start = output.tell()
            for record, name_bytes, local_offset, flags, dos_time, dos_date in central_rows:
                compressed_size = len(record.payload)
                uncompressed_size = record.original_size
                large_uncompressed = protected_ezip or uncompressed_size >= 0xFFFFFFFF
                large_compressed = protected_ezip or compressed_size >= 0xFFFFFFFF
                large_offset = protected_ezip or local_offset >= 0xFFFFFFFF
                if protected_ezip:
                    zip64_body = struct.pack(
                        "<QQQ", uncompressed_size, compressed_size, local_offset)
                    zip64_body += secrets.token_bytes(12 + secrets.randbelow(17))
                    central_extra = struct.pack(
                        "<HH", 1, len(zip64_body)) + zip64_body
                    total_extra_length = 202 + secrets.randbelow(53)
                    central_extra += secrets.token_bytes(
                        total_extra_length - len(central_extra))
                else:
                    central_extra = _zip64_extra(
                        uncompressed_size if large_uncompressed else None,
                        compressed_size if large_compressed else None,
                        local_offset if large_offset else None)
                needs_zip64 = bool(central_extra)
                method = 0 if file_system == "aem" and record.flags & 2 else 8
                if file_system == "aem":
                    version_made = 31
                    version_needed = 45 if needs_zip64 else (10 if method == 0 else 20)
                    crc = struct.unpack("<I", b"AEM!")[0]
                    external = 0x10 if record.flags & 1 else 0x20
                else:
                    version_made = (3 << 8) | (45 if needs_zip64 else 20)
                    version_needed = 45 if needs_zip64 else 20
                    crc = record.crc
                    external = ((0o40775 << 16) | 0x10
                                if record.flags & 1 else (0o100664 << 16))
                central_comment = b""
                disk_number = 0
                internal = 0
                if protected_ezip:
                    version_made = 0x033F
                    version_needed = 99
                    flags = 0x2049
                    dos_time = secrets.randbelow(0x10000)
                    dos_date = secrets.randbelow(0x10000)
                    crc = secrets.randbits(32)
                    disk_number = 1
                    internal = 0xFFFF
                    external = (0x40000007 if record.flags & 1 else 0x80000007)
                    central_comment = secrets.token_bytes(46 + secrets.randbelow(25))
                output.write(struct.pack(
                    "<4sHHHHHHIIIHHHHHII", b"PK\x01\x02",
                    version_made, version_needed, flags, method,
                    dos_time, dos_date, crc,
                    0xFFFFFFFF if large_compressed else compressed_size,
                    0xFFFFFFFF if large_uncompressed else uncompressed_size,
                    len(name_bytes), len(central_extra), len(central_comment),
                    disk_number, internal,
                    external,
                    0xFFFFFFFF if large_offset else local_offset))
                output.write(name_bytes)
                output.write(central_extra)
                output.write(central_comment)
            central_size = output.tell() - central_start
            entry_count = len(records)
            needs_zip64_archive = (entry_count >= 0xFFFF
                                   or central_start >= 0xFFFFFFFF
                                   or central_size >= 0xFFFFFFFF)
            if needs_zip64_archive and not incorrect_eocd:
                zip64_start = output.tell()
                output.write(struct.pack(
                    "<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0,
                    entry_count, entry_count, central_size, central_start))
                output.write(struct.pack(
                    "<4sIQI", b"PK\x06\x07", 0, zip64_start, 1))
            if incorrect_eocd:
                # Keep the actual ZIP records conventional.  Only the outer
                # SFX marker and end-of-directory metadata are deceptive: the
                # fixed ZIP64 count/offset remain usable by tolerant readers,
                # while its declared size and the classic EOCD are false.
                # The declared directory ends inside a random padding region.
                # A second equally-sized region follows it before ZIP64.
                size_lie = 3 * 1024 * 1024 + secrets.randbelow(1024 * 1024)
                remaining = size_lie * 2
                while remaining:
                    block_size = min(remaining, 1024 * 1024)
                    output.write(secrets.token_bytes(block_size))
                    remaining -= block_size
                zip64_start = output.tell()
                output.write(struct.pack(
                    "<4sQHHIIQQQQ", b"PK\x06\x06",
                    0x1000 + secrets.randbits(20),
                    0x031E, 99,
                    1, 2,
                    max(1, min(0xFFFF, entry_count // 3)), entry_count,
                    central_size + size_lie, central_start))
                # ZIP64 extensible data sector.  Strict ZIP readers include it
                # in end-of-archive validation; tolerant readers jump to the
                # locator immediately preceding the classic EOCD.
                output.write(secrets.token_bytes(512))
                output.write(struct.pack(
                    "<4sIQI", b"PK\x06\x07", 1 + secrets.randbelow(0xFFFF),
                    zip64_start, 1 + secrets.randbelow(0xFF)))
                comment = bytearray(secrets.token_bytes(1024))
                decoy_signatures = [
                    b"PK\x03\x04", b"PK\x01\x02",
                    b"PK\x06\x06", b"PK\x06\x07"]
                for position in range(16, len(comment) - 24, 64):
                    signature = secrets.choice(decoy_signatures)
                    comment[position:position + 4] = signature
                output.write(struct.pack(
                    "<4sHHHHIIH", b"PK\x05\x06", 1, 2, 0, 0,
                    0xFFFFFFFF, 0xFFFFFFFF, len(comment)))
                output.write(comment)
            else:
                output.write(struct.pack(
                    "<4sHHHHIIH", b"PK\x05\x06", 0, 0,
                    min(entry_count, 0xFFFF), min(entry_count, 0xFFFF),
                    min(central_size, 0xFFFFFFFF), min(central_start, 0xFFFFFFFF), 0))
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        if reporter is not None:
            reporter.finish_progress()


def salt_zero_stub_path(destination: Path) -> Path:
    """Return the sibling filename used for the unsalted loader archive."""
    return destination.with_name("stub_salt0.scs")


def write_salt_zero_stub(destination: Path) -> None:
    """Write the minimal compatibility ZIP containing only /base/def/."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".salt0_stub_", suffix=".tmp",
                                         dir=destination.parent, delete=False) as temporary:
            temp_name = temporary.name
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            archive.writestr("base/def/", b"")
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def parse_args(argv: list[str]) -> tuple[list[Path], list[Path], dict[str, set[str]], int, bool,
                                         Path | None, int, int | str, str, bool, set[str], bool,
                                         str, bool, bool, JunkOptions, list[str | None], bool,
                                         str | None]:
    argv = repair_windows_trailing_backslash_quoting(argv)
    if not argv or "--help" in argv or "-?" in argv:
        raise SystemExit(USAGE)
    input_paths: list[Path] = []
    output_paths: list[Path] = []
    output_option_specs: list[str | None] = []
    lists = {name: set() for name in "ehx"}
    forced_hidden: set[str] = set()
    verbose = 1
    include_empty_files = False
    include_directories = False
    restricted_options: list[str] = []
    zip_only_options: list[str] = []
    reference_path: Path | None = None
    salt = secrets.randbelow(0xFFFF) + 1
    salt_was_random = True
    salt_selected = False
    table_mode: int | str = "2t_2m_compressed"
    table_mode_selected = False
    file_system = "hashfsv2"
    compression_method = "max"
    dry_run = False
    sort_entries = False
    sort_payload = True
    junk_options = JunkOptions()
    crash_game: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "-i":
            values, index = input_arguments(argv, index)
            input_paths.extend(values)
        elif arg in ("-e", "-h"):
            restricted_options.append(arg)
            value, index = path_argument(argv, index, arg)
            lists[arg[1]].update(all_or_list_selection(str(value), arg))
        elif arg.startswith(("-e=", "-h=")):
            restricted_options.append(arg.partition("=")[0])
            option, _, value = arg.partition("=")
            if not value:
                raise ValueError(f"{option} requires 'all' or a list file")
            lists[option[1]].update(all_or_list_selection(value, option))
            index += 1
        elif arg in ("-fh", "--force-hide"):
            restricted_options.append(arg)
            value, index = path_argument(argv, index, arg)
            forced_hidden.update(force_hide_selection(str(value)))
        elif arg.startswith(("-fh=", "--force-hide=")):
            restricted_options.append(arg.partition("=")[0])
            option, _, value = arg.partition("=")
            if not value:
                raise ValueError(f"{option} requires a path-list file")
            forced_hidden.update(force_hide_selection(value))
            index += 1
        elif arg == "-o":
            values, specifications, index = configured_output_arguments(argv, index)
            output_paths.extend(values)
            output_option_specs.extend(specifications)
        elif arg in ("-x", "-ref"):
            restricted_options.append(arg)
            value, index = path_argument(argv, index, arg)
            if arg == "-ref":
                reference_path = value
            else:
                lists["x"].update(read_list(value))
        elif arg in ("-v", "-v0", "-v1", "-v2"):
            verbose = 2 if arg == "-v" else int(arg[-1])
            index += 1
        elif arg == "--verbose" or arg.startswith("--verbose="):
            value = "max" if arg == "--verbose" else arg.partition("=")[2].casefold()
            levels = {"min": 0, "bar": 1, "max": 2}
            if value not in levels:
                raise ValueError("--verbose requires min, bar or max")
            verbose = levels[value]
            index += 1
        elif arg in ("-a", "-af", "-ad"):
            include_empty_files |= arg in ("-a", "-af")
            include_directories |= arg in ("-a", "-ad")
            index += 1
        elif (arg in ("--entry-sort", "-es")
              or arg.startswith(("--entry-sort=", "-es="))):
            zip_only_options.append(arg.partition("=")[0])
            if "=" in arg:
                value = arg.partition("=")[2].casefold()
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{arg} requires enabled/disabled or y/n")
                value = argv[index + 1].casefold()
                index += 2
            aliases = {"enabled": True, "enable": True, "yes": True, "y": True,
                       "disabled": False, "disable": False, "no": False, "n": False}
            if value not in aliases:
                raise ValueError("Entry sort must be enabled/disabled or y/n")
            sort_entries = aliases[value]
        elif (arg in ("--sort-payload", "-spl")
              or arg.startswith(("--sort-payload=", "-spl="))):
            restricted_options.append(arg.partition("=")[0])
            if "=" in arg:
                value = arg.partition("=")[2].casefold()
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{arg} requires enabled/disabled or y/n")
                value = argv[index + 1].casefold()
                index += 2
            aliases = {"enabled": True, "enable": True, "yes": True, "y": True,
                       "disabled": False, "disable": False, "no": False, "n": False}
            if value not in aliases:
                raise ValueError("Payload sort must be enabled/disabled or y/n")
            sort_payload = aliases[value]
        elif arg == "--dry-run":
            restricted_options.append(arg)
            dry_run = True
            index += 1
        elif (arg in ("--crash-game", "-cg")
              or arg.startswith(("--crash-game=", "-cg="))):
            restricted_options.append(arg.partition("=")[0])
            if "=" in arg:
                value = arg.partition("=")[2]
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{arg} requires ByManifest or BM")
                value = argv[index + 1]
                index += 2
            aliases = {"bymanifest": "ByManifest", "bm": "ByManifest"}
            normalized = value.strip("'\"").casefold()
            if normalized not in aliases:
                raise ValueError("--crash-game/-cg requires ByManifest or BM")
            crash_game = aliases[normalized]
        elif arg == "--junk" or arg.startswith("--junk="):
            if "=" in arg:
                value = arg.partition("=")[2]
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError("--junk requires NAME=N; NAME=N")
                value = argv[index + 1]
                index += 2
            parse_junk_specification(value, junk_options)
        elif (arg in JUNK_CLI_OPTIONS
              or any(arg.startswith(option + "=") for option in JUNK_CLI_OPTIONS)):
            option = arg.partition("=")[0]
            restricted_options.append(option)
            if "=" in arg:
                value = arg.partition("=")[2]
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{option} requires an integer count")
                value = argv[index + 1]
                index += 2
            try:
                count = int(value)
            except ValueError as exc:
                raise ValueError(f"{option} count must be an integer") from exc
            setattr(junk_options, JUNK_CLI_OPTIONS[option], count)
        elif (arg in ("--file-system", "-fs")
              or arg.startswith(("--file-system=", "-fs="))):
            if "=" in arg:
                value = arg.partition("=")[2].casefold()
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{arg} requires hashfsv2, hashfsv1, ezip or aem")
                value = argv[index + 1].casefold()
                index += 2
            aliases = {"v1": "hashfsv1", "hashfsv1": "hashfsv1",
                       "v2": "hashfsv2", "hashfsv2": "hashfsv2",
                       "ez": "ezip", "ezip": "ezip",
                       "aem": "aem"}
            if value not in aliases:
                raise ValueError(
                    "File system must be hashfsv2/v2, hashfsv1/v1, ezip/ez or aem")
            file_system = aliases[value]
        elif arg.startswith(("-salt=", "--salt=")):
            restricted_options.append(arg.partition("=")[0])
            salt_selected = True
            value = arg.partition("=")[2]
            if value.casefold() == "random":
                salt = secrets.randbelow(0xFFFF) + 1
                salt_was_random = True
            else:
                salt_was_random = False
                try:
                    salt = int(value, 16 if value.lower().startswith("0x") else 10)
                except ValueError as exc:
                    raise ValueError(
                        "--salt requires random or an integer from 0 to 65535") from exc
                if not 0 <= salt <= 0xFFFF:
                    raise ValueError(
                        "--salt requires random or an integer from 0 to 65535")
            index += 1
        elif (arg in ("--compression-method", "-cm")
              or arg.startswith(("--compression-method=", "-cm="))):
            restricted_options.append(arg.partition("=")[0])
            if "=" in arg:
                value = arg.partition("=")[2].lower()
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(f"{arg} requires max, medium, min or no")
                value = argv[index + 1].lower()
                index += 2
            if value not in ("max", "medium", "min", "no"):
                raise ValueError("Compression method must be max, medium, min or no")
            compression_method = value
        elif arg in ("--table-mode", "-tm") or arg.startswith(("--table-mode=", "-tm=")):
            restricted_options.append(arg.partition("=")[0])
            table_mode_selected = True
            if "=" in arg:
                value = arg.partition("=")[2]
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError(
                        f"{arg} requires a v2 mode such as 1t2mum or random, "
                        "or 1..4 for HashFS v1")
                value = argv[index + 1]
                index += 2
            table_mode = int(value) if value in ("1", "2", "3", "4") else value
        else:
            raise ValueError(f"Unknown argument: {arg}")
    if not input_paths:
        raise ValueError("-i INPUT... is required")
    for input_path in input_paths:
        if not input_path.is_dir() and not input_path.is_file():
            raise ValueError(f"Input folder or file not found: {input_path}")
    if reference_path is not None:
        if not reference_path.is_dir() and not reference_path.is_file():
            raise ValueError(f"Reference folder or file not found: {reference_path}")
        if any(reference_path.resolve() == input_path.resolve()
               for input_path in input_paths):
            raise ValueError("-ref must be different from every -i source")
    if not output_paths:
        output_paths = [Path.cwd() / (input_paths[0].name + ".scs")]
        output_option_specs = [None]
    if len(output_paths) > len(input_paths):
        raise ValueError("The number of -o archives cannot exceed the number of -i sources")
    resolved_outputs = [path.resolve() for path in output_paths]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("Every -o archive path must be unique")
    if file_system == "hashfsv2":
        if zip_only_options:
            raise ValueError("--entry-sort/-es is supported only by eZIP and AEM")
        if isinstance(table_mode, int):
            raise ValueError(
                "HashFS v2 table mode must specify t/m positions, or random")
        table_mode = normalize_v2_table_mode(table_mode)
    elif file_system == "hashfsv1":
        if zip_only_options:
            raise ValueError("--entry-sort/-es is supported only by eZIP and AEM")
        if isinstance(table_mode, str):
            if table_mode_selected:
                raise ValueError("HashFS v1 table mode must be 1, 2, 3 or 4")
            table_mode = 1
    elif restricted_options:
        options = ", ".join(dict.fromkeys(restricted_options))
        raise ValueError(
            f"{file_system.upper()} supports only -i, -o, -a, -af, -ad, "
            f"--junk=\"EOCD=1; FE=N\" and "
            f"-v0/-v1/-v2/-v/--verbose; "
            f"unsupported option(s): {options}")
    validate_junk_options(junk_options, file_system)
    if crash_game is not None and file_system != "hashfsv1":
        raise ValueError("--crash-game/-cg is supported only by HashFS v1")
    if file_system == "hashfsv1" and not salt_selected:
        salt = 0
        salt_was_random = False
    return (input_paths, output_paths, lists, verbose, include_empty_files,
            reference_path, salt, table_mode, compression_method, dry_run,
            forced_hidden, salt_was_random, file_system, include_directories,
            sort_entries, junk_options, output_option_specs, sort_payload,
            crash_game)


def partition_input_groups(values: list, group_count: int) -> list[list]:
    """Split ordered inputs as 7/3 -> 3,3,1 while using every output."""
    target_size = (len(values) + group_count - 1) // group_count
    groups: list[list] = []
    cursor = 0
    for group_index in range(group_count):
        remaining = len(values) - cursor
        remaining_groups = group_count - group_index
        take = min(target_size, remaining - (remaining_groups - 1))
        groups.append(values[cursor:cursor + take])
        cursor += take
    return groups


@dataclass
class ArchiveOptions:
    lists: dict[str, set[str]]
    include_empty_files: bool
    reference_source: Path | None
    salt: int
    table_mode: int | str
    compression_method: str
    dry_run: bool
    forced_hidden: set[str]
    salt_was_random: bool
    file_system: str
    include_directories: bool
    sort_entries: bool
    sort_payload: bool
    junk: JunkOptions
    crash_game: str | None
    hide_requested: bool


def _options_from_parsed(parsed: tuple, raw_options: list[str]) -> ArchiveOptions:
    return ArchiveOptions(
        lists=parsed[2], include_empty_files=parsed[4],
        reference_source=parsed[5], salt=parsed[6], table_mode=parsed[7],
        compression_method=parsed[8], dry_run=parsed[9],
        forced_hidden=parsed[10], salt_was_random=parsed[11],
        file_system=parsed[12], include_directories=parsed[13],
        sort_entries=parsed[14], junk=parsed[15], sort_payload=parsed[17],
        crash_game=parsed[18],
        hide_requested=any(value == "-h" or value.startswith("-h=")
                           for value in raw_options))


def split_archive_option_string(value: str) -> list[str]:
    lexer = shlex.shlex(value, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[str] = []
    for token in lexer:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
            token = token[1:-1]
        tokens.append(token)
    return tokens


def per_archive_options(source: Path, destination: Path, specification: str,
                        verbose: int) -> ArchiveOptions:
    tokens = split_archive_option_string(specification)
    if any(token in ("-i", "-o", "-v", "-v0", "-v1", "-v2", "--verbose")
           or token.startswith("--verbose=") for token in tokens):
        raise ValueError(
            "Per-archive options cannot contain -i, -o or verbosity options")
    verbosity = ("min", "bar", "max")[verbose]
    parsed = parse_args([
        "-i", str(source), "-o", str(destination), *tokens,
        f"--verbose={verbosity}",
    ])
    return _options_from_parsed(parsed, tokens)


def report_archive_configuration(reporter: Reporter, options: ArchiveOptions) -> None:
    if options.file_system in ("ezip", "aem"):
        reporter.message(
            f"Options: file system={options.file_system}; "
            f"empty files={'included' if options.include_empty_files else 'skipped'}; "
            f"directory entries={'included' if options.include_directories else 'omitted'}; "
            f"entry sort={'enabled' if options.sort_entries else 'disabled'}; "
            f"incorrect EOCD={'yes' if options.junk.incorrect_eocd else 'no'}; "
            f"false entries={options.junk.false_entries}")
    else:
        junk_summary = ", ".join(
            f"{name}={count}" for name, count in options.junk.active().items()) or "none"
        reporter.message(
            f"Options: file system={options.file_system}; salt={options.salt}; "
            f"table mode={options.table_mode}; compression={options.compression_method}; "
            + (f"gdeflate={options.compression_method}; "
               if options.file_system == "hashfsv2" else "")
            + f"empty files={'included' if options.include_empty_files else 'skipped'}; "
            + f"empty directories={'included' if options.include_directories and not options.hide_requested else 'omitted'}; "
            + f"payload sort={'randomized' if options.sort_payload else 'source order'}; "
            + f"dry run={'yes' if options.dry_run else 'no'}; "
            + f"junk={junk_summary}; "
            + "decoy metadata descriptors="
            + f"{'yes' if options.junk.decoy_metadata_descriptor else 'no'}; "
            + f"crash game={options.crash_game or 'no'}")
    if options.file_system == "hashfsv1" and options.salt:
        reporter.message(
            "WARNING: A non-zero salt can make pooled lookup incompatible with "
            "other mod archives.")
        reporter.message(
            "A compatibility ZIP containing only /base/def/ will be created. "
            "It can be enabled in any Mod Manager position.")
    if options.file_system in ("hashfsv1", "hashfsv2"):
        reporter.message(
            f"Selectors: hide={'all' if ALL_SELECTOR in options.lists['h'] else len(options.lists['h'])}; "
            f"encrypt={'all .sii' if ALL_SELECTOR in options.lists['e'] else len(options.lists['e'])}; "
            f"force-hide={len(options.forced_hidden)}")
    if options.forced_hidden:
        reporter.message(
            "WARNING: --force-hide bypasses definition and map visibility checks. "
            "Forced files may be invisible to the game.")
    if (options.file_system in ("hashfsv1", "hashfsv2")
            and options.include_directories and options.hide_requested):
        reporter.message(
            "-ad is disabled for HashFS while -h is active, because explicit "
            "empty directory entries would expose hidden directory paths.")
    if options.compression_method == "no":
        reporter.message(
            "WARNING: --compression-method no stores HashFS file, directory and "
            "v2 texture payloads uncompressed; this significantly increases "
            "archive size.")


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or "--help" in raw_args or "-?" in raw_args:
        print(USAGE)
        return 0
    try:
        parsed = parse_args(raw_args)
        (sources, destinations, lists, verbose, include_empty_files, reference_source,
         salt, table_mode, compression_method, dry_run, forced_hidden,
         salt_was_random, file_system, include_directories,
         sort_entries, junk_options, output_option_specs, sort_payload,
         crash_game) = parsed
        global_options = _options_from_parsed(parsed, raw_args)
        archive_options = [
            (per_archive_options(sources[0], destination, specification, verbose)
             if specification is not None else global_options)
            for destination, specification in zip(destinations, output_option_specs)
        ]
        reporter = Reporter(verbose)
        version_names = {
            "hashfsv2": "HashFS v2",
            "hashfsv1": "HashFS v1",
            "ezip": "Ghostly Encrypted ZIP",
            "aem": "AEM",
        }
        selected_formats = list(dict.fromkeys(
            options.file_system for options in archive_options))
        version_label = ", ".join(
            version_names[file_system] for file_system in selected_formats)
        reporter.message(f"Universal SCS Packer - {version_label}")
        reporter.message(f"Inputs: {len(sources)}")
        for source_index, source in enumerate(sources, 1):
            reporter.message(f"  {source_index}. {source}")
        reporter.message(f"Outputs: {len(destinations)}")
        for output_index, (destination, options) in enumerate(
                zip(destinations, archive_options), 1):
            reporter.message(
                f"  {output_index}. {destination} [{version_names[options.file_system]}]")

        final_summaries: list[str] = []
        with ExitStack() as temporary:
            roots = [materialize_source(source, f"input {source_index}", temporary, reporter)
                     for source_index, source in enumerate(sources, 1)]
            reference_roots: dict[Path, Path] = {}
            for options in archive_options:
                if (options.reference_source is not None
                        and options.reference_source not in reference_roots):
                    reference_roots[options.reference_source] = materialize_source(
                        options.reference_source, "reference", temporary, reporter)
            game_paths: set[str] = set()
            if any(options.lists["h"] for options in archive_options):
                if not DEFAULT_GAME_PATHS_FILE.is_file():
                    raise ValueError(
                        f"Original-game path list not found: {DEFAULT_GAME_PATHS_FILE}")
                reporter.message("Loading original-game path list...")
                game_paths = read_list(DEFAULT_GAME_PATHS_FILE)

            indexed_roots = list(zip(sources, roots))
            input_groups = partition_input_groups(indexed_roots, len(destinations))
            all_source_map = overlay_file_sources(roots)
            for output_index, (input_group, destination, options) in enumerate(
                    zip(input_groups, destinations, archive_options), 1):
                lists = options.lists
                include_empty_files = options.include_empty_files
                reference_source = options.reference_source
                reference = (reference_roots.get(reference_source)
                             if reference_source is not None else None)
                salt = options.salt
                table_mode = options.table_mode
                compression_method = options.compression_method
                dry_run = options.dry_run
                forced_hidden = options.forced_hidden
                salt_was_random = options.salt_was_random
                file_system = options.file_system
                include_directories = options.include_directories
                sort_entries = options.sort_entries
                junk_options = options.junk
                crash_game = options.crash_game
                hide_requested = options.hide_requested
                version_label = version_names[file_system]
                group_sources = [source for source, _root in input_group]
                group_roots = [root for _source, root in input_group]
                reporter.message(
                    f"Output {output_index}/{len(destinations)}: {destination}")
                reporter.message(
                    "  Inputs: " + ", ".join(source.name for source in group_sources))
                report_archive_configuration(reporter, options)

                if file_system in ("ezip", "aem"):
                    reporter.message("Scanning ZIP input files...")
                    records = zip_records_from_sources(
                        group_roots, include_empty_files, include_directories,
                        sort_entries, reporter, file_system)
                    file_count = sum(not record.flags & 1 for record in records)
                    directory_count = len(records) - file_count
                    if not records:
                        raise ValueError("No entries can be written. Input is empty")
                    reporter.message(
                        f"ZIP entry preparation completed: {file_count} files, "
                        f"{directory_count} directories.")
                    reporter.message(
                        f"Writing {len(records)} archive entries ({file_count} files, "
                        f"{directory_count} directories)...")
                    if junk_options.false_entries:
                        reporter.message(
                            f"Adding {junk_options.false_entries} false ZIP entries...")
                    write_disguised_zip(
                        destination, records, file_system, reporter,
                        junk_options.incorrect_eocd,
                        junk_options.false_entries)
                    if junk_options.false_entries:
                        reporter.message(
                            "SUCCESS: False ZIP entries added: "
                            f"{junk_options.false_entries}")
                    archive_size = destination.stat().st_size
                    summary = (
                        f"SUCCESS: {version_label} archive created: {destination}\n"
                        f"  Entries: {len(records)} ({file_count} files, "
                        f"{directory_count} directories)\n"
                        f"  Archive size: {format_kilobytes(archive_size)}")
                    if len(destinations) == 1:
                        reporter.message("")
                        reporter.message("")
                        reporter.message(summary)
                    else:
                        final_summaries.append(summary)
                        reporter.message(
                            f"SUCCESS: {version_label} archive "
                            f"{destination.name} created successfully.")
                    continue

                if lists["h"]:
                    reporter.message("Checking hide-safety rules and dependencies...")
                    (visible, pruned_directories, mandatory_allowed, reference_allowed,
                     game_allowed, definition_allowed, mandatory_categories,
                     allowed_categories) = visible_protected_paths(
                        group_roots, lists["h"], reference, game_paths)
                    reporter.message(
                        "Protected categories always hidden when selected by -h:")
                    if mandatory_categories:
                        for category in sorted(mandatory_categories, key=str.casefold):
                            reporter.message(f"  {category}")
                    else:
                        reporter.message("  (none selected)")
                    reporter.message(
                        "Protected categories allowed by explicit text-file paths:")
                    if allowed_categories:
                        for category in sorted(allowed_categories, key=str.casefold):
                            reporter.message(f"  {category}")
                    else:
                        reporter.message("  (none)")
                    reporter.message("Scanning input files and building directory listings...")
                else:
                    visible = set()
                    pruned_directories = set()
                    mandatory_allowed = reference_allowed = 0
                    game_allowed = definition_allowed = 0

                if forced_hidden:
                    visible = {path for path in visible
                               if not is_selected(path, forced_hidden)}
                hidden = lists["h"] | forced_hidden
                group_source_map = overlay_file_sources(group_roots)
                (group_source_map, dependency_count,
                 reference_dependency_count, missing_texture_dependencies) = add_texture_dependencies(
                    group_source_map, all_source_map, reference)
                if dependency_count:
                    reporter.message(
                        f"Added {dependency_count} cross-input DDS dependency file(s) "
                        f"for TOBJ entries in output {output_index}.")
                if reference_dependency_count:
                    reporter.message(
                        f"Added {reference_dependency_count} DDS dependency file(s) "
                        f"from -ref for TOBJ entries in output {output_index}.")
                if missing_texture_dependencies:
                    if reporter.level >= 2:
                        for tobj_path, dds_path in missing_texture_dependencies:
                            reporter.message(
                                f"WARNING: External texture dependency /{dds_path} "
                                f"required by /{tobj_path}")
                    if reference is None:
                        reporter.message(
                            "WARNING: "
                            f"{len(missing_texture_dependencies)} TOBJ file(s) reference DDS "
                            "textures absent from the input. With a non-zero HashFS v2 salt, "
                            "these textures may fail in-game. It is recommended to extract "
                            "the game archives and provide their folder through -ref.")
                    else:
                        reporter.message(
                            "WARNING: "
                            f"{len(missing_texture_dependencies)} TOBJ file(s) reference DDS "
                            "textures absent from both the input and -ref. With a non-zero "
                            "HashFS v2 salt, these textures may fail in-game.")
                explicit_directories = set()
                if include_directories and not hide_requested:
                    for root in group_roots:
                        explicit_directories.update(directory_paths(root))
                items = collect_source_map(
                    group_source_map, group_roots[-1], lists["x"], hidden,
                    visible, pruned_directories, explicit_directories)
                source_files = sum(not item.directory for item in items)
                source_folders = len(items) - source_files
                if lists["h"]:
                    reporter.message(
                        f"Source scan complete: {source_files} files, "
                        f"{source_folders} folders.")
                reporter.message(
                    "Preparing HashFS records without compression..."
                    if compression_method == "no"
                    else "Preparing HashFS records and compressing eligible files...")
                if file_system == "hashfsv2":
                    records = records_from_items_v2(
                        items, lists["e"], compression_method, include_empty_files,
                        visible, pruned_directories, hidden, salt,
                        True, reporter)
                else:
                    records = records_from_items(
                        items, lists["e"], hidden, compression_method,
                        include_empty_files, visible, pruned_directories, salt, reporter)
                if junk_options.total():
                    reporter.message(
                        f"Adding {junk_options.total()} junk record(s)...")
                    generated_junk = add_junk_records(
                        records, junk_options, file_system, salt)
                    reporter.message(
                        "SUCCESS: Junk records added: "
                        + ", ".join(f"{name}={count}"
                                    for name, count in generated_junk.items()))
                if junk_options.decoy_metadata_descriptor:
                    for record in records:
                        if record.junk_kind != "UnknownChunk":
                            record.decoy_descriptor = True
                    reporter.message(
                        "SUCCESS: Decoy metadata descriptors inserted before "
                        "recognized descriptors.")
                if crash_game == "ByManifest":
                    manifests = [record for record in records
                                 if record.path.strip('/').casefold() == 'manifest.sii']
                    for record in manifests:
                        record.break_sizes = True
                    if manifests:
                        reporter.message(
                            "WARNING: manifest.sii declares compressed and uncompressed "
                            "sizes larger than its payload. The game will no longer start "
                            "with this mod.")
                    else:
                        reporter.message(
                            "WARNING: --crash-game=ByManifest was selected but "
                            "manifest.sii is not packed.")
                if options.sort_payload:
                    # Hash tables retain their required CityHash ordering;
                    # only physical payload placement is randomized.
                    secrets.SystemRandom().shuffle(records)
                record_files = sum(not record.flags & 1 for record in records)
                record_folders = len(records) - record_files
                payload_size = sum(len(record.payload) for record in records)
                reporter.message(
                    f"HashFS record preparation completed: {record_files} files, "
                    f"{record_folders} directory listings; payload "
                    f"{format_kilobytes(payload_size)}.")
                resolved_table_mode = (
                    random_v2_table_mode()
                    if file_system == "hashfsv2" and table_mode == "random"
                    else str(table_mode))
                if dry_run:
                    reporter.message(
                        f"Planning {len(records)} archive entries ({record_files} files, "
                        f"{record_folders} directory listings; payload "
                        f"{format_kilobytes(payload_size)})...")
                    if file_system == "hashfsv2":
                        (table_offset, metadata_offset, archive_size,
                        _entry_data, _metadata_data) = plan_archive_v2(
                            records, resolved_table_mode, reporter)
                    else:
                        table_offset, _insert_after, archive_size = plan_archive(
                            records, table_mode)
                        metadata_offset = None
                    reporter.message(
                        f"DRY RUN: archive was not written; estimated size "
                        f"{format_kilobytes(archive_size)}.")
                else:
                    reporter.message(
                        f"Writing {len(records)} archive entries ({record_files} files, "
                        f"{record_folders} directory listings; payload "
                        f"{format_kilobytes(payload_size)})...")
                    if file_system == "hashfsv2":
                        table_offset, metadata_offset = write_archive_v2(
                            destination, records, reporter, salt,
                            resolved_table_mode)
                    else:
                        table_offset = write_archive(
                            destination, records, salt, table_mode, reporter)
                        metadata_offset = None
                    archive_size = destination.stat().st_size

                if len(destinations) > 1:
                    action = "planned successfully" if dry_run else "created successfully"
                    reporter.message(
                        f"SUCCESS: {version_label} archive {destination.name} {action}.")

                create_salt_stub = file_system == "hashfsv1" and bool(salt)
                salt_stub = salt_zero_stub_path(destination)
                if create_salt_stub:
                    if dry_run:
                        reporter.message(
                            f"DRY RUN: compatibility ZIP stub would be created: {salt_stub}")
                    else:
                        write_salt_zero_stub(salt_stub)
                        reporter.message(
                            f"SUCCESS: Compatibility ZIP stub created: {salt_stub}")

                protected_visible = sum(
                    not item.directory and item.path in visible for item in items)
                forced_file_count = sum(
                    not item.directory and is_selected(item.path, forced_hidden)
                    for item in items)
                if (protected_visible or mandatory_allowed or reference_allowed
                        or game_allowed or definition_allowed):
                    reporter.message(
                        f"Hide safety: {protected_visible} protected files kept visible; "
                        f"{mandatory_allowed} allowed by mandatory categories; "
                        f"{reference_allowed} allowed by -ref; "
                        f"{game_allowed} allowed by original-game paths; "
                        f"{definition_allowed} allowed by explicit text-file paths.")
                if forced_hidden:
                    reporter.message(
                        f"WARNING: Force-hidden {forced_file_count} files using "
                        f"{len(forced_hidden)} selector(s); game visibility was not verified.")
                if pruned_directories:
                    reporter.message(
                        "Redundant root/original/reference directory listings omitted: "
                        f"{len(pruned_directories)}.")

                result_title = (
                    f"SUCCESS: Dry run completed; destination was not changed: {destination}"
                    if dry_run else
                    f"SUCCESS: {version_label} archive created: {destination}")
                summary = (
                    result_title + "\n"
                    f"  Entries: {len(records)} ({record_files} files, "
                    f"{record_folders} directory listings)\n"
                    f"  Archive size: {format_kilobytes(archive_size)}; "
                    f"table offset: 0x{table_offset:X}"
                    + (f"; metadata offset: 0x{metadata_offset:X}"
                       if metadata_offset is not None else "")
                    + (f"; table mode: {resolved_table_mode}"
                       if file_system == "hashfsv2" else "")
                    + (f"; salt: {salt}" if salt_was_random else ""))
                if len(destinations) == 1:
                    reporter.message("")
                    reporter.message("")
                    reporter.message(summary)
                else:
                    final_summaries.append(summary)
        if len(destinations) > 1:
            reporter.message("")
            reporter.message("")
            reporter.message("Final archive results:")
            for index, summary in enumerate(final_summaries):
                if index:
                    reporter.message("")
                reporter.message(summary)
        return 0
    except MemoryError:
        emit_early_message(
            "ERROR",
            "Not enough memory to complete packing. Reduce the maximum thread "
            "count, split the input into several output archives, or close other "
            "memory-intensive applications.")
        return 1
    except (OSError, ValueError) as exc:
        emit_early_message("ERROR", str(exc))
        return 1


def ensure_cli_console(gui_child: bool = False) -> None:
    """Bind streams for the windowed executable only when CLI output is needed."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        import msvcrt
        if gui_child:
            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                return
            descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
            stream = open(descriptor, "w", encoding="utf-8", errors="replace",
                          buffering=1, closefd=False)
            sys.stdout = stream
            sys.stderr = stream
            return
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleWindow() and not kernel32.AllocConsole():
            raise OSError("Unable to allocate console")
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace",
                          buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace",
                          buffering=1)
    except (AttributeError, OSError):
        pass


def launch_console_session(arguments: list[str]) -> None:
    command = [sys.executable, "--console-session", *arguments]
    flags = (subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
             if sys.platform == "win32" else 0)
    subprocess.Popen(command, creationflags=flags, cwd=Path.cwd())


if __name__ == "__main__":
    launch_arguments = sys.argv[1:]
    if not launch_arguments:
        try:
            from gui import run_gui
            exit_code = run_gui()
        except Exception:
            import traceback
            traceback.print_exc()
            exit_code = 1
    elif "--gui-child" in launch_arguments:
        ensure_cli_console(gui_child=True)
        launch_arguments = [argument for argument in launch_arguments
                            if argument != "--gui-child"]
        exit_code = main(launch_arguments)
    elif (sys.platform == "win32" and getattr(sys, "frozen", False)
          and "--console-session" not in launch_arguments):
        # Do not respawn a one-file PyInstaller executable here.  Its parent
        # process can remove the temporary _MEI bundle before the child opens
        # base_library.zip, causing an intermittent CLI-only startup failure.
        # Allocating the console in this process provides the same UX without
        # depending on the lifetime of that temporary extraction directory.
        ensure_cli_console()
        exit_code = main(launch_arguments)
        try:
            import msvcrt
            print("\nPress any key to continue...", end="", flush=True)
            msvcrt.getch()
            print()
        except (ImportError, OSError):
            pass
    elif "--console-session" in launch_arguments:
        ensure_cli_console()
        launch_arguments = [argument for argument in launch_arguments
                            if argument != "--console-session"]
        exit_code = main(launch_arguments)
        try:
            import msvcrt
            print("\nPress any key to continue...", end="", flush=True)
            msvcrt.getch()
            print()
        except (ImportError, OSError):
            pass
    else:
        exit_code = main(launch_arguments)
    sys.exit(exit_code)
