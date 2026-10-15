# 开发与贡献

项目按 MIT 公开维护，欢迎可复现问题及范围清楚的 PR。请使用合成数据描述问题，保留原作者及第三方版权，不上传案件、真实身份、日志中的私密内容或授权材料。

1. 阅读 `AGENTS.md` 与 `docs/source-boundaries.md`。
2. 按 README 用 Python 3.11 和哈希锁文件建立环境。
3. 先用合成数据复现问题，再做最小完整修改。案件隔离、金额、批次、照片及导出须在用户可观察边界验证。
4. 执行 `.venv/bin/python scripts/test.py`，在交付中注明检查及限制。v1 修改同步生成契约并执行 `.venv/bin/python scripts/export_api_contract.py --check`；前端修改在 `frontend/` 执行 `npm ci && npm run build && npm test`。需要浏览器、安装包或性能证据的修改须另补相应检查；合成 UI 入口为 `scripts/preview_synthetic.py`，性能口径见 `docs/performance.md`。
5. 变更依赖时修改输入文件，用下列命令生成锁文件，审查版本及哈希变化后同步环境：

```sh
uv --no-config pip compile requirements-dev.in --python-version 3.11 --universal --generate-hashes --default-index https://pypi.org/simple --output-file requirements-dev.lock
uv pip sync requirements-dev.lock
```

源码文件使用 UTF-8；Python 和脚本用 `snake_case`，测试用 `test_<behavior>.py`，文档用 `kebab-case.md`。目录按职责组织，不以开发者、修复次数或日期命名功能源码。历史恢复材料单独私有保存。

不要在普通测试中访问真实数据、现有服务或真实签名配置。不要运行原交接目录中的源码修补、数据库恢复或终止进程脚本。提交与发布遵守当前任务授权。

评审说明应包含：触发问题、最终行为、验证结果、涉及的格式兼容/迁移或恢复影响。没有运行的检查应明确标注。
