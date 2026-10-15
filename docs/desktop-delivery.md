# FundsTrace 独立桌面候选

状态：2026-10-15，本地开发候选。已增加独立 Electron 壳、Python sidecar、原生构建入口和 Windows x64 脚本；**Windows 尚未构建或运行，真实 Analytix 尚未接入，未签名或发布**。原目录、业务数据及 Analytix 保持不动，不上传 GitHub。常驻工作区重排见 [graph-workspace-plan.md](graph-workspace-plan.md)。

## 运行与数据边界

[desktop/main.cjs](../desktop/main.cjs) 只负责窗口、受限导航／下载和自己创建的服务；无 preload、无通用 IPC。G6 `/` 保留完整业务界面，`/analysis` 为分析，`/workspace/` 为共享 React 试点；React 尚未覆盖所有旧功能，不因桌面化删除旧入口。

[desktop_backend.py](../desktop_backend.py) 先从 stdin 读取严格的 `{protocol:1, token, dataDir, synthetic}`，再导入应用；token 为每次运行随机 64 位十六进制值，不进入 URL、argv、页面存储或日志。服务仅绑定 `127.0.0.1:0`，返回 `{protocol:1, port}`；Main 经认证健康检查后才加载页面，不接管固定端口上的既有服务。

认证中间件覆盖页面、API 和静态资源，核对 token、精确 Host、Origin／Fetch Metadata，并附加 CSP、no-store 等响应头。Main 的独立 session 只向自有窗口的精确 origin 注入认证头，拒绝未知网络、导航、窗口和权限；renderer 保持 sandbox、contextIsolation、webSecurity 开启，nodeIntegration 关闭。[Electron 安全规则](https://www.electronjs.org/docs/latest/tutorial/security)

正常运行的数据位于 Electron 自身 `userData/data`，不写安装资源，也不使用 Analytix 数据根。Main 通过 stdin EOF 请求自己的 sidecar 停止，超时仅终止持有句柄的子进程；服务崩溃显示故障，不自动重放导入。后台持久任务恢复仍由 Python 服务负责，退出 hook 不能代替断电／Windows 注销恢复。[app 生命周期](https://www.electronjs.org/docs/latest/api/app)

授权签名材料只允许正常模式从外部受保护环境提供，不打包。缺少配置保持未激活，显示激活页并拒绝业务 API；桌面运行认证不是产品激活许可。激活脚本已移入 `static/license.js` 以适配脚本 CSP。公开离线授权若迁移到公钥验签，应另作兼容任务，不在包内埋入共享 HMAC 秘密。

## 本地开发

使用 Python 3.11、Node 22.22.1；下列命令在候选根目录执行。Python 桌面锁含 PyInstaller 6.22.3，NPM 锁固定 Electron 44.7.2、electron-builder 26.15.3。首次网页构建仍按 README 安装前端依赖。

```sh
uv pip install --python .venv/bin/python --require-hashes -r requirements-desktop.lock
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run synthetic
```

`synthetic` 只在源码开发模式创建一次性目录：两组各 200 节点／199 条关系及空案件，正常退出后删除。该模式不继承业务数据根或真实授权配置，冻结后端和已封装壳都拒绝此模式。正常运行用 `npm --prefix desktop start`，不会自动激活或种入示例；开发 Python 可通过绝对路径 `FUNDS_TRACE_PYTHON` 指定。

本机 Electron 下载曾遇官方 GitHub TLS 失败，维护者按官方安装方式临时使用镜像并保留 Electron 内嵌官方校验和校验；未关闭 TLS／checksum 或修改系统代理。此为本机安装记录，不是默认镜像依赖或未来网络保证。[官方安装说明](https://www.electronjs.org/docs/latest/tutorial/installation)

## 原生构建与 Windows

[build_desktop_backend.py](../scripts/build_desktop_backend.py) 使用 PyInstaller onedir、控制台管道及显式资源白名单，包含 G6／Worker 等静态资源和 `frontend/dist`，排除合成种子入口；生成平台、架构和资源 SHA-256 清单。它不复制数据、授权或旧打包目录。[electron-builder.cjs](../desktop/electron-builder.cjs) 仅打包壳文件和原生后端，校验后端平台／架构与目标完全匹配，不自动发布。

```sh
.venv/bin/python scripts/build_desktop_backend.py
node desktop/tests/backend-smoke.cjs build/desktop/backend/funds-trace-backend
npm --prefix desktop run pack
```

上一轮 macOS arm64 已生成并启动未签名 `.app` 目录候选。本轮只重建原生后端、核对新增许可等 21 项资源并执行增强烟测，另检查源码 Electron 的窗口启动；不重复封装 Mac 应用。源码／资源修改后的旧 `.app` 不能证明新候选。目录启动与正式安装／发行验收分别记录，均未签名或发布。

Windows x64 本地 PowerShell 7 入口如下；脚本核对 Node／Python 进程为 x64，准备独立构建环境、按哈希锁同步、构建前端与原生 sidecar、运行桌面测试并生成目录候选，随后对**包内** backend 运行烟测。需已安装 Node 22.22.1、uv 与可用的 Python 3.11，目标用户安装运行不要求系统 Python。

```powershell
.\scripts\build-windows.ps1
.\scripts\build-windows.ps1 -Installer
```

输出在 `build/desktop/electron`；默认是未签名目录，`-Installer` 明确生成名称含 `unsigned` 的本地 NSIS 安装候选，普通用户安装且卸载保留应用数据。未配置签名或更新服务。PyInstaller 不是跨操作系统编译器，必须在 Windows 原生构建 Windows sidecar；macOS 的脚本检查／包目录不能证明 Windows 可运行。[PyInstaller 平台约束](https://pyinstaller.org/en/stable/index.html)、[electron-builder v26](https://www.electron.build/v26/docs/features/multi-platform-build/)

脚本校对包内 manifest 与每项静态资源，再以中文／空格临时资源路径启动包内程序，验证未激活入口、强制终止持有的子进程、同一空库重开和 stdin EOF 正常退出。它不读业务库、不继承测试外的授权材料，不宣称空库重开等于导入恢复。`native-backend-smoke.json`、`windows-candidate.json`／`.sha256` 仅记录版本、相对产物文件名、内容哈希和检查范围；中途失败不会生成新的 passed 报告。

本地候选 [windows-candidate.yml](../.github/workflows/windows-candidate.yml) 只允许手动触发：单 Windows job、中文源码目录、16 项现有 source 进程／恢复回归、上述原生构建入口、3 天产物留存。不使用业务数据、真实授权、签名、Release 或仓库写权限；仅显式保留 `win-unpacked` 和证据文件，不能上传整个 build／运行目录。当前没有远端仓库或 Actions 运行；获准上传并把工作流放到默认分支后，才可手动运行。[GitHub 手动运行条件](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

## Windows 实机验收顺序

本机为 macOS arm64，未发现可用的 Windows 主机、VM 或 PowerShell，因此下面各项目前均为 **NOT_EXECUTED（Windows）**。首次只跑一轮基础流程；新缺陷修复后复测受影响项，不反复重跑全部矩阵。

| 顺序 | 实机步骤与通过条件 | 证据边界 |
| --- | --- | --- |
| 1. 原生与中文路径 | Windows x64／PowerShell 7，在如 `C:\FundsTrace 验收\源码 项目` 的路径构建；运行包内 `.exe`，核对 native manifest、资源、烟测和目录候选哈希 | CI 可验证构建及 sidecar；不能据此填写 Electron GUI 已通过。 |
| 2. DPI 与密集表格 | 首先在 100% 系统缩放完成查询、勾选、前后流水、上图、邻域；再看 150%／200%，125% 在确有目标设备时补测。记录分辨率、系统缩放、应用缩放；确认底部滑轨、分页、焦点和完整金额可达，文字实际可辨 | 系统 DPI 与应用菜单缩放分别记录；CSS 视口、DPR 模拟或尺寸计算测试不替代 Windows 字体／Canvas 实测。 |
| 3. 原生保存／取消 | 合成 XLSX 和图像保存到中文／空格目录，XLSX 回读金额与币种，图像可打开；分别取消保存，不产生新文件并显示取消反馈 | React／G6、分析 HTML 和案件包各用其真实入口；不把“开始下载”当落盘完成，不上传导出文件或完整用户目录。 |
| 4. 退出与恢复 | 隔离测试用户下，导入进行中结束该测试实例及其归属服务后重开；中断任务显示错误、没有部分写入，重新预览确认后仅写一次；另查完成回执重读不重复导入 | 源码 `--synthetic` 每次创建新临时目录，不能用于跨启动保留任务的验收。封装业务页须具备维护者批准的隔离合成数据及测试授权；缺少时只验激活页，其余保持未执行。不得强杀按名称猜到的其他 Python 进程。 |
| 5. 安装与离线 | 有目录候选证据后，才按需生成 NSIS；干净测试用户安装、断网启动、卸载保留合成数据、重装重开；有第二版本时再验升级。混合 DPI 拖窗在具备双屏设备时执行 | 不安装到现有业务用户环境；不把当前未签名候选当公开发行，升级无候选时保持未执行。 |

Main 现在按目标显示器 `workArea` 的 DIP 限制初始窗口、最小宽高和居中位置，修复小逻辑工作区可能被 1440×940／最小 960×640 撑出屏幕的问题；不再次乘除 `scaleFactor`。这只覆盖窗口创建，运行中切换 DPI／移动显示器仍需上表实测。保留系统及应用缩放；10 px 密集表格若在目标设备确实难读，再按结果调整，不盲目继续减小字体。[Electron screen](https://www.electronjs.org/docs/latest/api/screen)、[Display 工作区单位](https://www.electronjs.org/docs/latest/api/structures/display)

## 当前证据与待验收

| 证据层 | 已有结果 | 明确限制 |
| --- | --- | --- |
| 本轮定向回归 | macOS：中文 cwd／数据根的 source sidecar、新库／升级库异常退出、真实应用启动恢复、案件外 HTTP 404 与回执幂等，16 项通过；窗口／宿主 Node 10 项通过 | Python 283、前端 37、旧版 Node 60 为上一轮结果，本轮不重复全套；未改 React、G6 业务逻辑或金额契约。 |
| 本轮 macOS 原生后端 | PyInstaller 构建，21 项资源与源码哈希相符；中文／空格目录、认证与未激活入口、持有子进程强制退出、同空库重开及 EOF 正常停服通过 | 报告为 `darwin/arm64`，不是 Windows、导入任务或解锁业务验收。 |
| 源码 Electron | 本轮新窗口规则下启动、合成 200／199 G6 与密集表格加载、正常退出通过；此前已验证分析、React、原生保存及取消 | 此前 XLSX 回读／下载证据仍限原候选；本轮未重复，不把它们当 Windows 结果。Worker 专项成功与回退仍未独立区分。 |
| 上轮 macOS `.app` 目录 | 上一候选 20 项资源和壳代码已核对，独立启动／激活 JS／退出通过 | 不是本轮新增许可资源与窗口规则后的包；本轮不为证明 Windows 而重复封装 Mac 应用。缺授权未解锁业务，未做签名、公证或 Windows 验收。 |
| Windows／发行 | 已有原生 x64 脚本与资源校验 | 尚未构建、运行、安装、签名或发布。需中文／空格路径、离线、DPI、保存取消、异常退出／恢复及升级证据。 |

下载保存须区分服务生成成功和磁盘落盘完成；Electron 使用原生保存对话框，并处理取消／中断，不允许任意远程 URL 或自动打开下载的 HTML。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item)

## 未来成为 Analytix 的一个导航项

长期入口是 [共享 React 面板](frontend-host-adaptation.md)＋宿主 adapter，不是把独立 Electron 或第二套数据库搬入 Analytix。`funds-panel.v1` 目前是 UI port；浏览器 `File`、`AbortSignal`、原值 DTO 不是已获接受的 IPC、权限或 protected-display 契约。G6 先保留独立页，后续按有界数据、创建／更新／销毁的控制器渐进抽离，不复制另一套资金聚合。

侧栏由**有效 contribution＋Core 已确认 enabled/available＋当前案件 session**派生；启停后重读状态，换案、同案重绑或撤销先清旧显示／回调。真实 Host 必须在用例入口核验能力、案件和代次，提供有限来源／保存动作及受保护显示；缺能力不回退独立 HTTP。保持一个 Go Harness、案件／快照 authority 和每个用例的一个数据所有者，不双写、不开共享 active-case 库。现有宿主缺口与固定 commit 研究保留在 [windows-and-plugin-plan.md](windows-and-plugin-plan.md) 和 [electron-boundary.md](electron-boundary.md)，未来实施前须重新核对实际 Host。
