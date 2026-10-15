# 独立版授权与签发工具

FundsTrace 保留安装后的离线激活。`FundsTrace Issuer` 是维护者使用的独立 Electron Windows 应用，具有独立 appId 和运行目录，不连接案件数据库。普通用户只安装 FundsTrace，复制机器码、取得授权码并输入激活。

新版 FT2 使用 Ed25519：签发端保存私钥，客户端只持验证公钥。旧 HMAC 仅在原部署明确配置 `FUNDS_TRACE_LICENSE_SECRET` 时继续兼容；不会把共享秘密加入公开安装器。Electron 的随机会话 token 仍只证明本地服务归属，不代替产品激活。

## 首次配置

1. 维护者打开 **FundsTrace Issuer**，输入至少 12 字符的私钥口令，选择“创建密钥对”。先为私钥和公钥选择两个新文件名；取消不生成文件，已有文件不覆盖。
2. 私钥以口令加密的 PKCS8 PEM 保存，仅由维护者保管和备份。工具退出后清除内存引用，下次须重新加载私钥。遗失私钥或口令不能从公钥恢复；不要把私钥放到源码、仓库变量、Actions 或安装资源里。
3. 将工具导出的 **SPKI 验证公钥**配置给客户端，任选其一：
   - 构建配置：GitHub 仓库 **Settings → Secrets and variables → Actions → Variables**，新增非秘密变量 `FUNDS_TRACE_LICENSE_PUBLIC_KEY`，内容为完整公钥 PEM；手动运行现有 Windows Actions。公钥经校验后进入客户端资源白名单。
   - 本地构建：将公钥保存为源码根的 `activation-public-key.pem`（已忽略），再运行 Windows 构建脚本；不要与上述环境变量同时使用。
   - 已安装版的维护配置：在 Windows 用户环境变量中设置 `FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE` 为公钥文件的绝对路径，完全退出并重新打开 FundsTrace。此显式配置优先于包内公钥；由部署维护者管理，不通过网页上传或导入接口设置。
4. 客户端激活页显示公钥指纹；与签发工具的指纹对照后开始签发。未配置或配置损坏时保持未激活，并给出公钥配置提示。

本轮未创建真实签发私钥，也未代替维护者配置生产公钥。未配置仓库变量时，公开预览包保持未配置状态；首次部署先完成上述公钥配置。合成回归使用的 RFC 8032 公开测试密钥不会作为默认受信公钥打包。

## 日常签发

1. 用户把 FundsTrace 激活页显示的机器码交给维护者。
2. 维护者在 Issuer 加载私钥，输入机器码，选择日期或永久授权，再复制完整 FT2 授权码给用户。
3. 用户输入授权码；客户端校验产品、签名、机器绑定和期限，保存到独立数据目录 `license.dat`，重启后重新校验。

FT2 日期授权包含所选本机日期全天；旧 HMAC 的截止语义不在本轮自动修改。两者均不防系统时间回拨。替换公钥会使旧公钥下的签名失效，应先安排重新签发。改主机名、系统标识或设备信息也可能改变机器码。

## 开发、构建与证据

```sh
npm --prefix desktop run issuer
npm --prefix desktop test
```

Windows 使用既有 `scripts/build-windows.ps1 -Installer`，一次构建客户端和独立 Issuer 两个未签名 NSIS。Actions 只接收公钥，不接收签发私钥或口令；原生 backend 烟测通过公开 RFC 测试种子执行 FT2 签发、激活、重开及错误机器／换公钥拒绝。

Windows GUI、系统 DPI、实际安装和私钥对话框交互不作为预览发布的通过标记。源码 Electron 窗口检查也不能代替 Windows 实机验收。

后续接入 Analytix 时按宿主契约提供免激活入口；本轮不添加绕过开关、不修改 Analytix，也不把独立客户端的激活状态当作宿主权限。
