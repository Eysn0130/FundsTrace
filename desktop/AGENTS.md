# desktop/ 局部规则

适用于独立 Electron 宿主、维护者 Issuer、安装器及本目录测试；先读根 AGENTS。根目录 `desktop_backend.py` 同属桌面行为边界，但不会因本文件位于 desktop 而自动受其路径作用域覆盖，修改 sidecar 时必须显式联读。所有相对验证命令从选定 checkout 的仓库根目录执行。

- 保留 `desktop/electron-builder.cjs` 定义的 FundsTrace appId、数据目录、升级身份与所属服务生命周期；不复制 Analytix 的 appId、注册表/更新 feed、签名身份或清理列表。安装/升级只关闭 FundsTrace 所有的服务，默认卸载保留案件数据；确认所有权不能靠笼统进程名终止其他程序。
- 根规则的 bootstrap token/stdin/Main-owned exact-origin 请求头边界同时适用于 Main、preload/renderer、下载和 sidecar。renderer 不持有认证 token；新增 IPC/导航/下载能力延续现有权限边界，不把免激活当取消认证或更新完整性检查的理由。
- 正常 v0.1.x 启动免激活，不以 `--synthetic` 或测试许可通过包内启动检查。`synthetic` 只供源码一次性预览；冻结后端拒绝该模式，不随包发布 seed 或业务状态。历史 alpha/FT2/HMAC 兼容及独立 `issuer/` 仍保留；Issuer 是维护者签发工具，不读取案件，私钥不进客户端，公共资源只含允许的 SPKI 公钥。
- 不因许可证或免激活调整删除旧 key/license/runtime 数据。许可材料、历史 MIT/第三方通知及 Issuer/应用顶层 LICENSE 的携带要求统一依 `docs/licensing.md`，不在本文件复制条款、给第三方新增授权或把路径白名单当运行访问控制。
- 在选定 checkout 根目录，日常入口是 `npm --prefix desktop test`；源码合成 GUI 为 `npm --prefix desktop run synthetic`。Electron NPM 包安装与运行时二进制可用是两份证据。原生 backend 使用 Python 3.11 与 `requirements-desktop.lock`；Windows x64 候选按 `scripts/build-windows.ps1`（PowerShell 7）和 `docs/desktop-delivery.md` 检查实际目录资源/backend；不能把 macOS backend 标作 Windows，也不由脚本/配置作者身份取得打包或 Actions 执行授权。
- 修改宿主联查 Main/权限/下载/更新相关 Node 测试；修改 sidecar/资源联查后端资源检查及构建配置/资源清单等必要调用方。影响安装、升级、卸载或更新安装链、安装器/更新包的清单与资源装载契约时，补相应目标系统安装入口证据；仅改更新检查提示或状态展示，按受影响源码检查与实际用户流程验证，不据此默认正式打包。记录候选 SHA、版本与资源身份及未验收范围；源码 synthetic、包内 crash smoke、静默安装与真实 Windows GUI/DPI/系统保存/导入恢复分别报告，不互相替代。发行、Tag、Release、国内同步及公共说明沿根规则和 CONTRIBUTING，均须当前授权。
