# Third-party notices and source provenance

FundsTrace is maintained in this repository by Eysn0130 and contributors. It continues a pre-existing colleague-developed application; the initial repository import is a maintenance snapshot and does not assert sole authorship of that earlier work. Existing author and copyright notices are retained. The project license is MIT, as selected by its maintainer.

## G6

The retained G6 4.8.19 browser bundle is maintained with one documented local validation patch. Copyright (c) 2018 Alipay.inc; MIT. The complete notice is in `static/vendor/G6-LICENSE`; patch scope and exact local hashes are in `static/vendor/G6-PATCH.md`. This notice does not identify the local pre-patch file as an independently verified upstream tarball.

## Tabler Icons

The workspace uses 24 outline SVGs from `@tabler/icons` 3.35.0, downloaded from the npm registry with the published SHA-512 integrity verified. Copyright (c) 2020–2025 Paweł Kuna; MIT. Their source files and complete notice are retained in `static/ui-icons/`. The 2026-10-15 workspace refinement changes only `chevron-down.svg` stroke color to `#bbcada` so the same licensed arrow is readable as a local select background; other icon paths are unchanged. Upstream: https://github.com/tabler/tabler-icons. These are local resources; rendering does not request an external icon service.

The import workflow also retains the unmodified `check.svg` from Tabler Icons v3.35.0 (`icons/outline/check.svg`, https://github.com/tabler/tabler-icons/tree/v3.35.0), under the same MIT notice in `static/ui-icons/LICENSE`. It is a bundled local resource.

The import-batch table uses the unmodified `dots.svg` from the same Tabler Icons v3.35.0 source (`icons/outline/dots.svg`) under that MIT notice, bundled locally.

## FundsTrace ribbon icons

The additional compact ribbon icons in `static/ui-icons/` (`network`, `selection`, `selection-clear`, `swap`, `list`, `group`, `link`, `layers`, `chart`, `route`, `play`, `star`, `shape`, `photo`, `align-left`, `align-top`, `distribute-vertical`, `lock`, `focus`, `zoom-out`, `zoom-in`, `zoom-reset`, `eye`, `eye-off`, `eye-restore`, `legend`, `map`, `calendar`, `target`, and `font-text`) were drawn for FundsTrace on 2026-10-16. They use the project MIT license and the same 24px outline grammar as the retained Tabler controls. No external icon service or runtime dependency is used.

## React and React DOM

The React pilot bundles React and React DOM. Their exact versions are fixed in `frontend/package-lock.json`. Both carry the following MIT notice:

MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Python, Electron and build dependencies

Python runtime dependency versions and distribution hashes are recorded in `requirements-desktop.lock`. Native backend builds recursively retain installed runtime distribution metadata and their supplied license files. Bundled fonts retain their upstream notices. Electron distributions retain `LICENSE` and `LICENSES.chromium.html`. Build and test dependencies keep their own licenses; the FundsTrace MIT license does not replace those licenses. No project authorization secret or business dataset is part of the distribution.

## Analytix Windows installer flow

`desktop/installer.nsh` adapts installation-root process scoping and installer lifecycle hooks from Analytix `build/standard-win-installer.nsh` at commit `c7d2a370855a8d371c61c32cc2422515b9404ef2` (https://github.com/Eysn0130/analytix). Copyright 2026 Guoqin He (GitHub: Eysn0130); Apache License 2.0, reproduced in `desktop/licenses/Apache-2.0.txt` and distributed as `resources/licenses/Analytix-Apache-2.0.txt`. FundsTrace modifications dated 2026-10-15 retain its per-user installation identity and default data retention, display the installed version, and request normal window closing rather than forcing process termination. FundsTrace does not use Analytix registry keys, update feeds or signing identity.

## Brand typography

The FundsTrace wordmark uses Manrope and Noto Sans SC, both under SIL Open Font License 1.1. The fixed source versions, copyright notices and full licenses are recorded in [docs/brand/README.md](docs/brand/README.md) and [docs/brand/licenses/](docs/brand/licenses/). Published SVGs contain glyph outlines; font binaries are not included in this repository.
