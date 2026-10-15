# 开发与贡献

项目按 [MIT](LICENSE) 公开维护，欢迎可复现问题和范围清楚的 PR。先读 [AGENTS.md](AGENTS.md)、[源码与私有数据边界](docs/source-boundaries.md)，再按 [README](README.md#可重复安装和测试) 建立环境；README 维护安装和运行入口，这里约定协作方式。

## 新成员从一个合成用例开始

使用 README 指定的 Python 3.11、Node 基线和锁文件安装依赖，先运行一个独立合成用例，确认理解案件包往返行为：

```sh
.venv/bin/python scripts/test.py tests/test_cases.py::test_package_round_trip_preserves_duplicate_rows_and_all_associations -q
```

Windows 将 Python 路径换为 `.venv\Scripts\python`。需要看界面时，按 README 构建前端后运行 `scripts/preview_synthetic.py`，使用它打印的一次性地址和样例；不要连接已有业务服务。代码职责见 [README 代码地图](README.md#代码地图与兼容性)。

## 分工和 PR

- `main` 保持可交付；从最新 `main` 建短分支，每个 PR 完成一个可说明、可验证的行为变化，合并后结束该分支。
- 开始前在 Issue、PR 或团队任务中写清问题、验收行为和认领的模块／文件。多人并行时独占写入范围，保留他人修改；尤其避免同时编辑大文件 `app/main.py` 和 `static/app.js`。确需交叉修改时先协调顺序，再由后一位接续最新改动。
- 修缺陷先给最小合成复现，说明步骤、预期和实际结果。可行时用公共行为回归覆盖原问题，确认修复前失败、修复后通过；期望值来自独立合成样例。
- 做最小完整修改，连同必要调用方、测试和文档一起交付。新增抽象、依赖或兼容路径须有当前需求；无关重构另开任务，不用临时绕过、弱化断言或重复实现掩盖缺陷。
- PR 说明问题、行为变化、实际验证及兼容／数据影响，使用仓库模板。真实协作者到位后，由维护者按模块安排一位相关 reviewer，并设置必要的 required checks；当前不虚构 owner，不要求固定双审或每个 PR 默认跑全套。

## 按影响选择最小充分验证

| 变更范围 | 需要的证据 |
| --- | --- |
| 纯说明文档 | 检查差异、引用和路径；不因文档改动启动全套测试或打包 |
| Python 业务逻辑 | 通过 `scripts/test.py` 运行受影响模块／用例，例如 `.venv/bin/python scripts/test.py tests/test_cases.py -q` |
| 案件隔离、金额、导入或数据契约 | 对应公共行为回归；覆盖变更涉及的跨案拒绝、精度／币种、失败回滚／恢复及兼容边界 |
| v1 API | 执行 `.venv/bin/python scripts/export_api_contract.py --check`，补调用方受影响回归；契约变化时从 Python schema 同步生成 `contracts/` 两份输出，不手改生成类型 |
| React／G6 界面 | 相关前端／合成导航回归及受影响用户流程；React 变更执行 `npm --prefix frontend run build`，新环境或锁文件变化先按 README 执行 `npm ci` |
| 桌面宿主或分发 | 相关 `npm --prefix desktop test` 检查；涉及安装、升级、卸载时补实际安装入口证据，不能用 API 或源码测试替代 |

跨模块修改或共享基础行为变化可能需要完整 `.venv/bin/python scripts/test.py`；按实际影响扩大检查，而非机械叠加。性能变更遵循 [性能口径](docs/performance.md)，不要把正确性烟测当计时结论。记录执行命令、结果及未验证的边界，区分源码、浏览器、包内后端和安装后行为；相关检查通过且无新疑点后停止重复验证。

## 依赖、来源与代码约定

变更 Python 依赖时修改相应输入文件，重新生成对应锁文件，审查版本和哈希差异。开发锁文件的命令为：

```sh
uv --no-config pip compile requirements-dev.in --python-version 3.11 --universal --generate-hashes --default-index https://pypi.org/simple --output-file requirements-dev.lock
uv pip sync requirements-dev.lock
```

Node 依赖同步所属目录的 `package.json` 和 `package-lock.json`，使用 `npm ci` 复现安装。保留 MIT、原作者和第三方版权；新增或更新外部代码、资源和依赖时核对来源、版本及许可要求，必要时更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，不直接替换锁定的 vendor 文件。

源码文件使用 UTF-8；Python 和脚本用 `snake_case`，测试用 `test_<behavior>.py`，文档用 `kebab-case.md`。目录按职责组织，不以开发者、修复次数或日期命名功能源码。

## 数据和发布边界

Issue、PR、测试、日志和截图只使用合成数据；不上传案件、真实账户／身份／交易、授权记录、凭据或签名材料。公开报告用合成复现和必要环境信息，不粘贴原始业务日志。不要读取原 `fund-tracker` 业务目录，不在普通测试中访问真实数据、现有服务或真实签名配置，也不要运行历史修补、恢复或终止进程脚本。

桌面版本仅由维护者按当前授权打 Tag、手动触发 [Windows Actions](.github/workflows/windows-candidate.yml) 并发布；提交 PR 本身不触发发版。每个 `v0.1.x` Tag 对应带 Windows x64 `.exe` 的 Release，具体策略及未验收边界以 [桌面交付](docs/desktop-delivery.md) 为准。Git 提交、push、打包和外部写入仍遵守当前任务授权。
