Bundled archive-reading backend
================================

Universal SCS Packer uses the 7-Zip command-line backend to read 7z, RAR and
the other archive formats reported by `7z.exe i`. ZIP and TAR-family archives
are normally handled by Python's standard library first.

Bundled version: 7-Zip 24.09 (x64), 2024-11-29
Files: 7z.exe, 7z.dll
Upstream: https://www.7-zip.org/

See License.txt for the licenses and the unRAR restriction that apply to the
bundled binaries. The backend is used for extraction only; the packer's output
continues to be SCS HashFS v2 or HashFS v1.
