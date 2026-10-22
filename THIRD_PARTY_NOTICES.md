# Third-party notices and source provenance

FundsTrace is maintained in this repository by Eysn0130 and contributors. It continues a pre-existing colleague-developed application; the initial repository import is a maintenance snapshot and does not assert sole authorship of that earlier work. Existing author and copyright notices are retained. The maintainer originally selected MIT; its complete project notice is preserved unchanged in `LICENSES/MIT.txt`. On 2026-10-21 the maintainer adopted the FundsTrace Community Use and Source License 1.0 for content within the team's authority to license: the Chinese text in `LICENSE` controls, and `LICENSE.en` is the complete English translation. This source-available license does not withdraw existing MIT permissions, including for MIT-licensed content retained in a later snapshot, or replace any third-party license. Scope and the transition boundary are documented in `docs/licensing.md` and `docs/licensing.en.md`.

The maintainer has confirmed non-exclusive contribution authorization from CodeHaven969 and linzijie6524 and their special permission under section 7 of `LICENSE` for continued Agent development and integration into their own closed-source or commercial products. This does not establish authorization for another author's work or waive third-party obligations. In particular, the colleague-developed source provenance is retained without assuming those contributors' authorization covers every original component.

中文说明：当前许可为《循迹 FundsTrace 社区使用与源码许可协议 1.0》，中文正文 `LICENSE` 为准，英文完整对照见 `LICENSE.en`；中英文适用说明见 `docs/licensing.md` 和 `docs/licensing.en.md`。历史 MIT 原文及既有权利继续保留，所有第三方组件仍按各自原许可使用。两位指定贡献者的特别授权仅覆盖团队实际有权授予的内容，不据此推定原同事或其他作者的授权。

## G6

The retained G6 4.8.19 browser bundle is maintained with one documented local validation patch. Copyright (c) 2018 Alipay.inc; MIT. The complete notice is in `static/vendor/G6-LICENSE`; patch scope and exact local hashes are in `static/vendor/G6-PATCH.md`. This notice does not identify the local pre-patch file as an independently verified upstream tarball.

## Tabler Icons

The workspace uses 24 outline SVGs from `@tabler/icons` 3.35.0, downloaded from the npm registry with the published SHA-512 integrity verified. Copyright (c) 2020–2025 Paweł Kuna; MIT. Their source files and complete notice are retained in `static/ui-icons/`. The 2026-10-15 workspace refinement changes only `chevron-down.svg` stroke color to `#bbcada` so the same licensed arrow is readable as a local select background; other icon paths are unchanged. Upstream: https://github.com/tabler/tabler-icons. These are local resources; rendering does not request an external icon service.

The import workflow also retains the unmodified `check.svg` from Tabler Icons v3.35.0 (`icons/outline/check.svg`, https://github.com/tabler/tabler-icons/tree/v3.35.0), under the same MIT notice in `static/ui-icons/LICENSE`. It is a bundled local resource.

The import-batch table uses the unmodified `dots.svg` from the same Tabler Icons v3.35.0 source (`icons/outline/dots.svg`) under that MIT notice, bundled locally.

## FundsTrace ribbon icons

The additional compact ribbon icons in `static/ui-icons/` (`network`, `selection`, `selection-clear`, `swap`, `list`, `group`, `link`, `layers`, `chart`, `route`, `play`, `star`, `shape`, `photo`, `align-left`, `align-top`, `distribute-vertical`, `lock`, `focus`, `zoom-out`, `zoom-in`, `zoom-reset`, `eye`, `eye-off`, `eye-restore`, `legend`, `map`, `calendar`, `target`, and `font-text`) were drawn for FundsTrace on 2026-10-16. Their recorded MIT license is retained in `LICENSES/MIT.txt`; they use the same 24px outline grammar as the retained Tabler controls. No external icon service or runtime dependency is used.

## Windows updater and release publisher

The desktop host includes `electron-updater` 6.8.9 (electron-builder contributors, MIT); its locked production dependencies and their license files are bundled by electron-builder. The cloud publication tool uses Alibaba Cloud's `ali-oss` 6.23.0 SDK (Alibaba Group, MIT) only in the release job; it is not included in installers. Exact versions, transitive dependencies and registry integrity hashes are retained in `desktop/package-lock.json` and `scripts/release-publisher/package-lock.json`.

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

Python runtime dependency versions and distribution hashes are recorded in `requirements-desktop.lock`. Native backend builds recursively retain installed runtime distribution metadata and their supplied license files. Bundled fonts retain their upstream notices. Electron distributions retain their upstream `LICENSE` and `LICENSES.chromium.html`. Build and test dependencies keep their own licenses; neither the FundsTrace community license nor its historical MIT notice replaces those licenses. No project authorization secret or business dataset is part of the distribution.

## Analytix Windows installer flow

`desktop/installer.nsh` adapts installation-root process scoping and installer lifecycle hooks from Analytix `build/standard-win-installer.nsh` at commit `c7d2a370855a8d371c61c32cc2422515b9404ef2` (https://github.com/Eysn0130/analytix). Copyright 2026 Guoqin He (GitHub: Eysn0130); Apache License 2.0, reproduced in `desktop/licenses/Apache-2.0.txt` and distributed as `resources/licenses/Analytix-Apache-2.0.txt`. FundsTrace modifications dated 2026-10-15 retain its per-user installation identity and default data retention, display the installed version, and request normal window closing rather than forcing process termination. FundsTrace does not use Analytix registry keys, update feeds or signing identity.

## FundsTrace application icon and historical typography

The current application icon is derived from the maintainer-approved raster image retained as `docs/brand/source/app-icon-approved.png`. Its transparent master and generated application, web and GitHub resources are documented in [docs/brand/README.md](docs/brand/README.md). The application icon does not depend on a font or external icon service.

The earlier FundsTrace wordmarks used Manrope and Noto Sans SC, both under SIL Open Font License 1.1. Those wordmark assets have been retired; their copyright notices and full licenses remain in [docs/brand/licenses/](docs/brand/licenses/). Font binaries are not included in this repository, and replacing the application icon does not change those upstream licenses.
