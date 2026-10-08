# ADR 目录（架构决策记录）

沿用 Michael Nygard 的 ADR 惯例（公开发表的经典实践）：每篇记录一个不可逆度较高
的决策——背景、决策、后果。历史快照语义：既定决策不追改，新决策新增编号；
推翻旧决策时新增一篇并注明取代关系。

| 编号 | 决策 | 状态 |
|---|---|---|
| [ADR-0001](adr-0001-sqlite-single-file.md) | 存储层采用 SQLite 单文件（WAL） | 已接受（v10 起运行） |
| [ADR-0002](adr-0002-solver-dual-path.md) | 求解器双路径：穷举兜底 + 精确前沿互验 | 已接受（v13/v14） |
| [ADR-0003](adr-0003-interval-not-in-passport.md) | 减排区间不进决策护照 | 已接受（v14） |
| [ADR-0004](adr-0004-no-ruff-linter.md) | 不引入 ruff 静态检查（仅评估结论） | 已接受（v16 评估） |
| [ADR-0005](adr-0005-matching-preview-no-persistence.md) | 撮合建议即算即返不落库（含升级为落库的触发条件） | 已接受（v16 实现，v17 补记） |
| [ADR-0006](adr-0006-async-jobs-in-memory.md) | 大案例异步作业为进程内存态（重启即失，如实告知） | 已接受（v18 实现，v19 补记） |
