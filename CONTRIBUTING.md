# 贡献指南 · CONTRIBUTING

感谢关注供需透镜（SupplyLens）。本项目以中文为权威文档语言（设计、审计、运维与
商业物料均随源更新），Issue 与 PR 可用中文或英文。

## 项目约束（先读）

- **离线确定性**：全部输入为本地 CSV/YAML，全部计算为确定性算法（相同输入 →
  逐字节一致输出，测试锁定）；禁止引入在线调用、随机性或新的运行时依赖
  （`requirements.txt` 固定 `PyYAML` 与 `ortools` 的直接依赖版本）。
- **数值冻结锚点**：试点固定案例基准 83,025 CNY → 组合 64,182 CNY、净减排
  21,072.98 kgCO2e；组合前沿最低 59,941.25 CNY。引擎（`src/material.py`、
  `src/ledger.py`、`src/matching.py`）任何改动必须「优化前后数值逐位一致」或
  给出等价论证，并以独立穷举核验互验。
- **演示与真实分离**：演示数据全部虚构并显著标识；不得伪造客户、交易、检测
  报告、减排量或试点结果。

## 两套环境（勿混用）

| 环境 | 用途 | 说明 |
|---|---|---|
| Python ≥3.12 的完整环境 | 全量测试、独立核验、试点与完整前沿 | 安装 `requirements-test.txt`（包含运行依赖与 pytest）；CI 使用 3.12/3.14 |
| Windows 浏览器环境 | 浏览器 E2E、截图、发行验证 | 安装 `requirements-dev.txt`（包含运行/测试依赖与 Playwright），需要本机 Chrome/Edge；旧 `.e2e-venv` 缺少 OR-Tools 的回退结果不能当成完整求解结果 |

```bash
# 全量测试（零跳过）+ 1 个演示案例和 6 组随机案例的独立穷举核验
python -m pip install -r requirements-test.txt
python scripts/check_project.py

# 用词门禁（改任何文档/站点文案后）
python -m pytest tests/test_docs_wording.py -q

# Windows：四套真实 Chrome E2E（console 零错误）
.e2e-venv\Scripts\python tests\e2e_v12.py
.e2e-venv\Scripts\python tests\e2e_round2.py
.e2e-venv\Scripts\python tests\e2e_v16.py
.e2e-venv\Scripts\python tests\e2e_v18.py
```

E2E 偶发网络层抖动（RST/元素超时）原样重跑一次；连续两次同点失败才按缺陷排查。

完整零跳过验收在 Linux/WSL 执行。Windows 没有创建符号链接权限时会跳过两个
文件边界用例；须如实报告，不能当成完整验收。Windows 浏览器与独立发行验证仍需完成。

## 提交规范

- 每个任务一个 commit、独立可回退；首行说明「做了什么」，含关键验收数字。
- 用户可见变更必须同步 `CHANGELOG.md`（Keep a Changelog 分类：Added / Changed /
  Fixed / Removed），并附验收数字。
- 文档与站点文案受用词门禁约束（历史陈旧口径词禁回流）；移动/重命名/删除文件
  必须同步全部引用（`git grep` 实测兜底）。

## 发行白名单纪律

`dist/` 双包只能由 `scripts/build_release.py` 从白名单重建（`DEMO_FILES` /
`PILOT_FILES` / `PILOT_EVIDENCE` 等，位于该文件顶部）。**新增任何模块、页面、
测试或文档，必须先登记白名单再构建**——未登记文件不会进入发行包，包内引用将
在构建验证时失败。构建后运行 `scripts/verify_release.py`（14 项独立验证）。

## 分支与同步

外部贡献请从 GitHub `main` 拉取分支并发起 PR。维护者的本地完整历史、内部材料、
运行数据与第三方 PDF 原件不随公开源码发布。公开导出与发布步骤见
`docs/开源发布指南.md`；不要直接推送完整本地 `master` 或全部 tags。

## 提交前自查清单

1. 两套环境各自验收全绿（单测 0 跳过、独立核验全过、门禁全过；动浏览器层后四套 E2E）。
2. 冻结锚点逐字符未变（动引擎必查）。
3. `CHANGELOG.md` 已更新；新文件已登记发行白名单。
4. 注释只写行为约束与不变式，不含改动自述。

## 许可

提交贡献即表示你有权提交这些内容，并同意按本项目 MIT 许可证发布。
第三方代码或资料须注明来源及适用许可；来源公开可读不能替代再分发授权。
