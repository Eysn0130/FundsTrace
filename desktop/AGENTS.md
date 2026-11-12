# desktop/ 局部规则

继承根 AGENTS；适用 Electron、Issuer、安装器及本目录测试。根 desktop_backend.py 同属行为边界，修改时显式联读本文件；目录作用域不会自动覆盖根 sidecar。

- electron-builder.cjs 的 appId／升级身份和服务生命周期保留，不借 Analytix 的注册表／更新 feed／签名身份；安装／升级只关 FundsTrace 所属服务，默认卸载留案件数据，不能靠笼统进程名判断所有权。Main、preload／renderer、下载及 sidecar 延续根规则的 token／origin 边界，新增 IPC／导航／下载不扩大权限。
- issuer/ 是离线维护者工具，不读案件；免激活不授权删除旧 key／license／runtime。Issuer 与应用的顶层 LICENSE 携带依 `docs/licensing.md`，不把资源白名单当运行访问控制，历史 FT2／HMAC 与签发材料依 `docs/activation-history.md`。
- 日常 `npm --prefix desktop test`，源码合成 GUI 为 `npm --prefix desktop run synthetic`；NPM 包安装与 Electron 二进制可用是两份证据。原生 backend 用 Python 3.11／requirements-desktop.lock，Windows x64 按 scripts/build-windows.ps1（PowerShell 7）与 `docs/desktop-delivery.md` 核实际资源；macOS backend 不标成 Windows。
- 宿主改动联查 Main／权限／下载／更新测试；sidecar／资源改动联查后端资源检查、构建配置及清单。安装／升级／卸载／更新安装链或装载契约变化补目标系统安装入口；只改提示／状态按受影响源码和实际用户流验证，不默认打包。记录候选、版本、资源身份及未验范围，synthetic、包内 smoke、静默安装与 Windows GUI／DPI／系统保存／恢复分别报告，发行权限沿根规则及 CONTRIBUTING。
