# FundsTrace 品牌资产

当前应用图标使用维护者确认的 PNG 方案。桌面应用、浏览器页头与标签页、GitHub README 和社交预览均从同一批准来源派生，保留图标自身的底色与图形；深浅主题使用相同图像。主品牌名为 **FundsTrace**，中文说明为 **资金流水追踪与关联分析**。

![FundsTrace 品牌板](brand-board.png)

## 来源与母版

- [批准原图](source/app-icon-approved.png) 保留维护者选定的原始栅格方案，作为来源记录。
- [透明母版](source/app-icon-master.png) 保留批准方案的应用图标主体与底色，将图标外部背景处理为透明，作为各尺寸资源的统一母版。
- [render.mjs](render.mjs) 是应用图标和品牌展示资源的统一生成入口。更新时先确认来源与母版，再重新生成派生文件，避免各页面自行重绘或变色。

## 选用资产

| 用途 | 文件 | 规格 |
| --- | --- | --- |
| GitHub README | [应用图标](icons/icon-256.png) | 256 × 256，页面按需缩小 |
| 浏览器页头与 React 工作台 | [Logo](../../static/logo.png) | 256 × 256，同一图标 |
| 浏览器标签页 | [ICO](../../static/favicon.ico)、[PNG](../../static/favicon-32.png) | 多尺寸 ICO、32 × 32 PNG |
| 桌面应用与安装器 | [PNG](../../desktop/assets/icon.png)、[Windows ICO](../../desktop/assets/icon.ico) | PNG 与多尺寸 ICO |
| PNG 尺寸集 | [16](icons/icon-16.png)、[20](icons/icon-20.png)、[24](icons/icon-24.png)、[32](icons/icon-32.png)、[40](icons/icon-40.png)、[48](icons/icon-48.png)、[64](icons/icon-64.png)、[96](icons/icon-96.png)、[128](icons/icon-128.png)、[256](icons/icon-256.png)、[512](icons/icon-512.png)、[1024](icons/icon-1024.png) | RGBA，图标外透明 |
| 通用 Windows 图标 | [ICO](icons/icon.ico) | 与桌面应用共用 |
| 品牌板 | [PNG](brand-board.png) | 图标与尺寸使用示意 |
| GitHub 社交预览 | [PNG](../media/fundstrace-social.png) | 与应用图标共用母版 |

保持正方形比例，不裁掉图标边缘，不额外添加底板、阴影、文字或主题滤镜。小尺寸直接使用对应 PNG／ICO 档位。旧版 SVG 标识、字标与主题变体已停用并移除。

## 重建

```sh
BRAND_SHARP_PATH=/path/to/node_modules/sharp \
BRAND_CANVAS_PATH=/path/to/node_modules/@napi-rs/canvas \
node docs/brand/render.mjs --latin-font /path/to/latin.ttf --cjk-font /path/to/cjk.ttf
```

需要 `sharp 0.35.5`、`@napi-rs/canvas 0.1.100`；两个库路径可省略，此时使用正常 Node 模块解析。字体仅用于品牌板和社交预览文字，默认使用 macOS Avenir Next／Hiragino Sans GB，可用参数替换；应用图标仅缩放母版。生成过程在本地运行，不调用图像生成服务，也不需要历史字标字体。产物包括 `icons/` 的 PNG／ICO、`static/logo.png`、`static/favicon.ico`、`static/favicon-32.png`、`desktop/assets/icon.png`、`desktop/assets/icon.ico`、1600 × 1040 品牌板和 1280 × 640 社交预览图。ICO 包含 16／20／24／32／40／48／64／96／128／256 像素共十档。

## 来源与许可记录

当前批准原图是维护者在本次设计会话选定的图像生成方案，透明母版通过同一图像工具去除图标外围的展示背景与投影，不重绘内部标识；不包含业务数据或原单位标识。图标的来源、母版与派生文件一起保留，后续设计变更不得以低清截图替代母版。

历史字标曾使用 [Manrope](https://github.com/google/fonts/tree/b31870aff700ab7a1d74fa0c6887d95beb9e0037/ofl/manrope) 650 字重与 [Noto Sans SC](https://github.com/google/fonts/tree/a85815a42757630ce188fdad368c2dfc444d4773/ofl/notosanssc) 400 字重；两者均使用 SIL Open Font License 1.1。旧字标资源已停用，版权和完整许可证继续保留在 [Manrope-OFL.txt](licenses/Manrope-OFL.txt)、[NotoSansSC-OFL.txt](licenses/NotoSansSC-OFL.txt)。字体二进制没有加入本仓库，当前应用图标不使用这些字体。

## 桌面集成边界

桌面宿主和安装器使用同一组 PNG／ICO 资源。此次图标替换不改变 appId、产品名、数据目录或升级身份。源码资源变更不会追溯修改任何已经发布的 EXE；旧版发布资产继续保留其原图标。新候选仍需在自身构建及实际 Windows 安装、任务栏和系统缩放证据中核对图标，源码与浏览器预览不能代替这些检查。

## 主页业务展示图

主页保留可复用的[业务流程 SVG](../media/fundstrace-workflow.svg)／[2× PNG](../media/fundstrace-workflow.png)与[本机架构 SVG](../media/fundstrace-architecture.svg)／[2× PNG](../media/fundstrace-architecture.png)。它们是逻辑示意，导入／分析展示使用真实合成预览截图；首页启动动图来自已确认设计预览的持续等待态。具体来源和验证见[素材核验](../showcase-validation.md)。

[render-showcase-figures.mjs](render-showcase-figures.mjs) 仅重建这两张图，需要 `@napi-rs/canvas 0.1.100`、`sharp 0.35.5` 与本地可用的拉丁／中文字体。通过 `SHOWCASE_CANVAS_PATH`、`SHOWCASE_SHARP_PATH` 可指定已有库路径，用 `--latin-font FILE --cjk-font FILE` 指定字体。SVG 字体转为路径，页眉内嵌同一 PNG 图标，无外部字体或图片请求；字体文件不随图分发。当前成图使用 macOS Avenir Next／Hiragino Sans GB。

[render-showcase-media.py](render-showcase-media.py) 需要 Pillow 12.3.0，读取 `docs/media/showcase/` 内已采集的 PNG，生成首屏展示图、导入／分析 GIF 及静态封面。截图只等比缩放，字幕、边框和进度在产品画面之外；没有 MP4 中间文件。可用 `--font FILE` 指定中文字体，当前成图使用 macOS STHeiti Light。

```sh
node docs/brand/render-showcase-figures.mjs --latin-font /path/to/latin.ttf --cjk-font /path/to/cjk.ttf
python docs/brand/render-showcase-media.py --font /path/to/cjk.ttf
```

这些脚本不重建应用图标、不修改业务或桌面资源，也不生成安装包。
