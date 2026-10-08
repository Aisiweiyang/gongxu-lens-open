# 变更日志 · CHANGELOG

公开版本从 **v0** 开始，后续会不定期更新和维护。下方 v13–v27.1 为开源前的内部迭代记录，
不代表公开仓库的发行版本。历史记录按原编号保留。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的分类（Added / Changed /
Fixed / Removed），每轮附验收数字与数值冻结锚点。更细的问题—根因—修改—测试—结果记录见
各轮 `docs/自审-vXX.md`；文档地图见 `docs/INDEX.md`。

数值冻结锚点（跨轮不变，E2E 数值断言为准绳）：试点固定案例基准 **83,025** CNY →
组合 **64,182** CNY，净减排 **21,072.98** kgCO2e；静态演示案例组合前沿最低 **59,941.25** CNY。
算法版本 material-v8；护照 schema v2.0.0。

## [v0] — 2026-10-08 · 初始开源版

### Added
- 首次公开供需透镜源码，采用 MIT 许可证，建立独立公开仓库。
- 包含再生材料筛选、采购组合与情景核算、试点工作台及测试文档。

### Changed
- 重写中英文 README，精简重复链接和内部迭代叙事，按初始开源项目介绍功能、安装方法和使用边界。
- 感谢秦仲远和杨笑墨的协助工作。
- 将自动生成的演示页和 E2E 建议单标为生成文件，排除出 GitHub 语言统计，保留应用入口。
- 明确后续不定期更新和维护，暂无固定更新周期；公开版本与内部迭代编号分别记录。

## 开源前内部迭代记录

## [v27.1] — 2026-10-08 · 开源准备与静态服务修复

### Fixed
- 演示静态服务统一 GET/HEAD 的解码、真实路径与符号链接边界检查，禁止目录列表；
  HTTP 错误状态消息改用 ASCII，启动目录固定为源码目录。
- 当前双语说明与核验徽章纠正为“1 个演示案例 + 6 组随机案例”。历史轮次的
  “47/47”原记录保留供追溯，但当前独立脚本不能重现该覆盖，不作为发布验证依据。
- 移除发行验证脚本中本机旧项目解释器路径。
- 浏览器全流程等待真实任务创建完成，并实际删除供给再验证备份恢复；失败时释放
  测试 Chrome。回滚演练在替换 Database 实例前释放恢复后重新打开的连接，避免 Windows 文件锁。

### Added
- MIT LICENSE 与第三方声明；双语安装步骤、固定运行依赖版本、完整测试依赖清单。
- 共用验收入口（缺依赖或跳过均失败）、Linux Python 3.12/3.14 CI 与公开源码导出。
- 静态服务 9 项回归与公开范围 4 项回归；授权文件同步进入发行白名单。

### Changed
- 公开导出排除内部参赛/过程文件、业务数据、旧生成报告与第三方 PDF 原件，保留本地资料。
- 安全报告统一使用 GitHub 私密报告入口；正式公开前由维护者启用并验证。
- 本轮最终测试、干净安装和发行验证结果见 `docs/开源准备验收-20261008.md`。

## [v27] — 2026-09-18 · 安全加固与可交付版本

用户自主开发授权轮（「完善成可正式使用的版本」）。交接基线 267/267 + 47/47
复验通过。验收：WSL pytest **271/271**（0 跳过）· 独立穷举核验 **47/47** ·
门禁 4/4 · E2E **17/17 + 18/18 + 31/31 + 9/9** · 发行 VERSION=v27，
verify_release **14/14**（demo 60 / pilot 169 文件）。引擎计算语义零改动。

### Added
- **试点应用安全加固**：全部响应携带安全响应头（nosniff / X-Frame-Options
  DENY / Referrer-Policy no-referrer / CSP frame-ancestors none），静态页
  完整 CSP 限定本源；API 响应一律 no-store；会话 Cookie 升级 `SameSite=Strict`
  并经反向代理头自动追加 `Secure`；登录时序均衡（用户不存在也执行等量 pbkdf2
  推导，不泄露账号存在性）；口令策略升级为至少 8 位且含字母与数字（测试夹具
  同步换用合规口令，规则未让步）；演示站同步补齐同类响应头。新增
  `tests/test_v26_security.py` 4 项回归，测试基线 267 → **271**；安全清单
  增「响应安全头与口令策略」节（逐条挂代码位置），SECURITY.md 同步。
- **开源准备：过程文档归档与交付包瘦身**：23+1 件 AI 协作过程文档
  （优化提示词-SPEC-v14…v27、自审-v13…v26、仓库结构盘点-v15）git mv 归档至
  `docs/archive/process/`（移而不删，互引保持原文）；现行引用同步 9 文件零残留；
  发行白名单剔除 17 件过程文档（pilot 包 185→169，交付包不再携带协作过程
  记录）；开源前敏感信息复扫零命中；转公开剩余步骤清单入所有者手册
  （LICENSE 拍板/SECURITY 邮箱/output-archive 遗留去留/转公开+Pages）。
- **因子影子试算首次执行（六度门控双前提首次同时达成）**：openLCA EcoProfiles
  rPP 真因子（CC BY 4.0，当日实检通过，PDF 原件入库）；影子运行器
  `scripts/shadow_trial.py`（锚点断言防扰动，生产零触碰）；真实因子下净减排
  方向不变（-3.6%）、区间传播首次真实数据运行、引擎边界兼容性检查正确拦截
  口径冲突因子并暴露演示配置边界标注不一致（接入前置条件）；同日第二轮补齐
  基准侧（ACC/Franklin Associates 原生 PP cradle-to-gate 1,548 kgCO2e/t，公开
  PDF 实检下载），双侧真因子场景净减排 16,313.61 kgCO2e（较演示口径 -22%，
  方向与方案排序不变）；核算约定修订两方案草案（A 窄口径/B 宽口径）列明待
  拍板；天工本地源当日可达性实检记录；报告
  `docs/影子试算-EcoProfiles-rPP-20260917.md`。
- **三端深色模式**：跟随系统 `prefers-color-scheme` 自动切换（绿系暗色令牌、
  SVG 垫白兜底、打印强制亮色）；全流程人工走查截图存档 `docs/e2e-shots/v26-dark/`，
  `tests/test_v26_dark_mode.py` 锁四端回归，深色辅助文字对比度达 WCAG AA。
- `scripts/demo_selfcheck.py` 答辩前演示环境一键自检（9 项，含双包哈希逐文件
  核对；报告写 `docs/演示自检-报告.md`）。
- `启动试点工作台.bat`（双击启动工作台，与 启动演示网站.bat 同模式，随包分发）。
- 文档体系：`docs/项目完整介绍.md`（一份讲全）、`docs/使用手册-所有者版.md`
  （所有者日常与资产管理）、`docs/优化提示词-SPEC-v27.md`（下一轮输入：决策
  落定与答辩准备）。
- GitHub：v18–v21 四枚 Release 补全（共八枚）、主题标签扩至 12 枚、ISSUE/PR
  模板、徽章链接化、v26-final Assets 附双包 zip 下载（与仓库同步重建）。

### Changed
- **评委网站呈现精修（零功能改动）**：主标题渐变文字（@supports 渐进增强，
  暗色换浅色系、打印还原纯色）、hero 点阵纹理层、决策结论条强调圆点、
  **步骤导航条吸顶毛玻璃常驻**（steps 移出 hero section 以解除 sticky 父盒
  约束，选择器不变，四套 E2E 验证）；工作台空态占位图形；仪表盘 board 卡片
  悬浮描边。三档视口溢出 0px。
- **代码注释去过程叙事（全代码面）**：剥离注释/docstring 中的内部审计编号
  （P0-x/A0x/F-xx/C0x/B02）与「修复版/修复要点/前车之鉴」类改动自述约 70 处，
  改写为行为约束与不变式表述（约束内容全保留；同类历史叙述本就在
  CHANGELOG/自审可查）；测试类名与其用途描述不变。
- 站点 head 补 `meta description`；撮合建议单钉定亮色 `color-scheme`。
- 测试基线 266 → **267**（全链同步徽章与当前口径文档）。

### Fixed
- dist 包内 README 徽章破图（白名单补徽章与首屏截图）。
- 全仓口径一致性审计：使用指南第二节自 v17 后首次刷新（至当前终态）、README
  双语 tag 上界与能力节陈旧修正、INDEX 活文档清单补 `演示自检-报告.md`。

## [v26] — 2026-09-16 · README 呈现补强与发行包修正轮

用户直接指示续作（无 SPEC 文档），记录 `docs/自审-v26.md`。验收：WSL pytest
**266/266**（0 跳过）· 独立核验 **47/47** · 门禁 4/4 · E2E **17/17 + 18/18 +
31/31 + 9/9** · 发行 VERSION=v26，verify_release **14/14**（demo 60 / pilot 183 文件）。引擎计算语义零改动。

### Fixed
- **发行包内 README 徽章破图**（v23 起 README 相对路径引用 `.github/badges/`
  但白名单未含）：白名单两包补入四枚徽章 SVG 与首屏截图，包内 README 呈现
  完整。

### Added
- 两版 README 首屏截图（评委网站 1440 视图，含 alt 文本）。
- 站点 head 补 `meta description`（与 og:description 同文案，离线约束不变）。

## [v25] — 2026-09-16 · 呈现细节与工程卫生轮

用户直接指示续作（无 SPEC 文档），记录 `docs/自审-v25.md`。验收：WSL pytest
**266/266**（0 跳过）· 独立穷举核验 **47/47** · 门禁 4/4 · E2E **17/17 + 18/18 +
31/31 + 9/9** · 发行 VERSION=v25，verify_release **14/14**。引擎计算语义零改动。

### Added
- **三端品牌 favicon**：统一内联 SVG（与站点徽标同源），data URI 严格编码保持
  单文件离线约束；site 与工作台替换空占位、仪表盘补缺失。
- **站点 Open Graph 元数据**：og:title/description/type 文字元数据。

### Fixed
- 零外部资源门禁两处拦截的修正：og:image 外链移除（红线 2 不降门禁）；data
  URI 编码修正（首版单引号未转义致 href 属性截断）。`test_no_external_resources`
  （site/dashboard）精确化：icon 仅允许 data: 内联、http(s) 检查剥离合法内联
  URI——测试意图（零外部网络引用）不变。

### Changed
- 三端 `color-scheme:light` 声明：防系统暗色下原生表单控件深色渲染破坏界面
  一致性。
- `.gitattributes`：显式二进制标注与 `.bat` CRLF 强制（纯卫生声明，不改既有
  文件行尾）。

## [v24] — 2026-09-16 · 去 AI 痕迹与呈现精修轮

用户直接指示续作（无 SPEC 提示词文档），记录 `docs/自审-v24.md`。验收：WSL
pytest **266/266**（0 跳过）· 独立穷举核验 **47/47** · 门禁 4/4 · E2E **17/17 +
18/18 + 31/31 + 9/9** · 发行 VERSION=v24，verify_release **14/14**。引擎计算
语义零改动。

### Changed
- **三端呈现精修**（零 DOM，只动渲染层）：评委网站 hero 光斑层、KPI 数字渐变
  短线与间距、卡片 hover 位移、链接色统一；离线仪表盘分区卡片化（board 区块
  白底圆角阴影）与标题竖条装饰；试点工作台 KPI 渐变顶条、表格行左侧强调条。
  11–13 号素材随新外观重摄（01–10 未触碰，红线 6）。

### Verified
- **去 AI 生成痕迹两轮全仓扫描**：AI 腔调词/装饰性 emoji/机械排比/营销空泛词/
  内部协作词在现行区（docs + 根 md + site + pilot/static + src）零命中或均为
  合法引用与业务语境；v5/v7 专项清理纪律持续有效。过程文档归档与否列为待
  用户拍板事项（红线 9）。

## [v23] — 2026-09-15 · 开源准备与呈现升级轮

输入 `docs/优化提示词-SPEC-v23.md`（新会话交接版），记录 `docs/自审-v23.md`。
交接自检基线 266/266 + 47/47 复验通过。验收：WSL pytest **266/266**（0 跳过）·
独立穷举核验 **47/47** · 门禁 4/4 · E2E **17/17 + 18/18 + 31/31 + 9/9**（真实
Chrome）· 发行 VERSION=v23，verify_release **14/14**。LICENSE 待用户拍板未落
文件（SPEC 闸门：未拍板只搭骨架）；CI 未获确认不接入；任务 6（因子影子试算）
门控未过，五度搁置。引擎计算语义零改动，任务 3 数值层经剥离样式块逐字符比对
确认零变化。

### Added
- **开源三件套骨架**（任务 1）：`CONTRIBUTING.md`（项目约束/两套环境/测试门禁/
  提交规范/发行白名单纪律/快照同步指引/提交前自查清单）与 `SECURITY.md`
  （支持版本/本地优先架构安全边界/漏洞报告渠道占位/响应预期）；LICENSE 待
  用户拍板（MIT / Apache-2.0 / 维持私有）。
- **GitHub 仓库呈现**（任务 4）：README 两版顶部静态文本徽章（仓库内自绘
  SVG 四枚，零外部 CDN，不造假 CI 徽章）；社交预览图 `.github/social-preview.png`
  （1280×640 文字排版方案，需仓库所有者在 Settings 手动上传）。
- 改版前基线截图 6 张（`docs/e2e-shots/v23-baseline/`）与基线截图脚本
  `scripts/shots_v23_baseline.py`（任务 3）。

### Changed
- **三端页面高级化与商业化**（任务 3，主任务，零 DOM 改动只动渲染层）：统一
  设计令牌（绿主色/字阶/间距/圆角/阴影双层）；评委网站首屏商业化（hero 三段
  渐变、KPI 等宽大数字、主按钮渐变、表格与卡片密度升级）与窄视口回落；离线
  仪表盘 CSS 全套令牌化（原灰系朴素样式升级为绿主题卡片/表格/打印/响应式，
  清理无 DOM 使用的死规则）；试点工作台登录页居中品牌化、表格密度与 KPI 数字
  层级。11–13 号素材随新外观重摄（01–10 未触碰，红线 6）。
- **docs 个人路径批量清理**（任务 2）：SPEC-v14–v23 八件文首加开源说明标注 +
  路径泛化；现行文档五件正文泛化（含数据源启用步骤失效命令改为通用写法）；
  archive 快照仅文首加标注正文不动；「清华绿创赛/」暂存区未触碰。

### Fixed
- 390px 视口横向溢出回归（任务 3 过程中由 `th nowrap` 引入，当轮定位修复，
  三档视口实测溢出归零，四套 E2E 全绿后提交）。
- 12 号截图呈现口径补互引（任务 5，自审-v22 遗留 2 闭环）：截图索引标注表内
  为 v16 串行口径快照，并行化后数字见压力基准第六节。

## [v22] — 2026-09-15 · 口径刷新与收尾打磨轮

输入 `docs/优化提示词-SPEC-v22.md`（新会话交接版），记录 `docs/自审-v22.md`。
交接自检基线 261/261 + 47/47 复验通过。验收：WSL pytest **266/266**（0 跳过，
含 5 项新增标签槽回归）· 独立穷举核验 **47/47** · 门禁 4/4 · E2E **18/18 + 31/31 +
17/17 + 9/9** · 发行 VERSION=v22，verify_release **14/14**。任务 4（因子影子试算）
门控未过，四度搁置。

### Added
- **大案例端到端实测**（任务 3）：并行化后单发引擎步 **549.2s**（串行 809.0s 的
  1.47×，全步骤零错误，约 9–10 分钟），替换 v19 推算值 550–560s；原始数据
  `benchmarks/压力基准-raw-v22parallel-wsl-shared.{json,md}`（入发行白名单）。
- `docs/自审-v22.md`；开源准备清理扫描结论入档（无敏感信息/无产物跟踪，
  个人路径批量清理列入 SPEC-v23）。

### Changed
- **中文 README 口径刷新至 v21 事实**（主任务）：新增「当前能力（v13–v21）」与
  「实测性能」两节；「测试」重写为「测试与验证」；方法说明补独立核验/建议单/
  异步；快速开始补试点工作台；v10/v11 升级节加「历史口径」标注；英文版 tag
  序列修正 v21-final。
- 商用试点手册 §7 增并行化后大案例预期等待（实测 549s ≈ 9–10 分钟）；
  耗时结构-v18 推算值标注已被实测替换；11–13 号素材随修复重摄。

### Fixed
- **SVG 标签槽自适应**（任务 2，v17 遗留）：`n1_service_svg`/`interval_svg` 长标签
  按最长标签动态加宽标签槽（上限 240px），超上限截断加省略号、`<title>` 保留
  全名；只改渲染层，数值输出零变化；13 号截图遗留截断消失。
- **CHANGELOG v18 节结构复位**：v19 终验恢复标题时误置于正文之后（正文滞留
  v19 节内且引言重复，v20/v21 沿袭），复位为「标题→引言→正文」并合并引言。

## [v21] — 2026-09-14 · 双语管理轮

按用户指示：GitHub 仓库与项目双语化。验收：门禁 4/4 · 全量测试与核验不受影响
（纯文档/元数据轮，基线维持 v20 的 261/261 + 47/47）· 发行 VERSION=v21，
verify_release **14/14**。

### Added
- `README.en.md`：英文版项目概览（定位/架构/快速开始/方法/验证与质量/实测
  性能/安全要点/数据契约/文档地图英文导读/状态与边界），按 v20 当前事实撰写。
- 语言政策：中文为权威源文档，英文 README 为对照概览；README.md 与 INDEX.md
  顶部声明语言切换与政策。

### Changed
- GitHub 仓库描述双语化并补充主题标签；发行白名单加 `README.en.md`；
  VERSION 升 v21。

## [v20] — 2026-09-14 · 商业准备轮

输入 `docs/优化提示词-SPEC-v20.md`，记录 `docs/自审-v20.md`。验收：WSL pytest
**261/261**（0 跳过）· 独立穷举核验 **47/47** · 门禁 4/4 · E2E 不变全绿 ·
发行 VERSION=v20，verify_release **14/14**。

### Added
- `docs/安全与合规应答清单-v20.md`：试点 IT/采购/数据审核六节检查单，证据式
  写作（每条挂代码位置，当日代码扫描核实），含已知边界如实声明。
- `docs/试点部署指南-Linux-v20.md`：Ubuntu/Debian 部署、备份策略、systemd、
  验收清单（WSL 验证口径与裸机未实测如实分栏）。
- `scripts/pilot_selfcheck.py` + `tests/test_v20_selfcheck.py`：零依赖只读
  试点自检（解释器/文件/数据库/备份/磁盘/健康端点），容错降级不崩溃。
- `docs/试点一页纸方案模板-v20.md`：面向园区的客户物料（全部待确认标注）。

### Fixed
- 试点默认库路径表述统一修正为 `pilot/data/pilot.sqlite3`（与 start_pilot
  一致；此前文档误写 `data/`）。

## [v19] — 2026-09-14 · 场景级并行化与答辩备询轮

输入 `docs/优化提示词-SPEC-v19.md`，记录 `docs/自审-v19.md`。验收：WSL pytest
**258/258**（0 跳过）· 独立穷举核验 **47/47** · 门禁 4/4 · E2E **18/18 + 31/31 +
17/17 + 9/9** · 发行 VERSION=v19，verify_release **14/14**。任务 4（因子影子试算）
门控未过，按纪律跳过。

### Added
- **场景级并行化**：`run_sensitivity` 场景并行 + `_build_result` 双情景 payload/
  敏感性三单元并发（stdlib 线程池，`SUPPLYLENS_PARALLEL_WORKERS` 可覆盖）。
  30 供给完整分析 **430.8s → 179.3s（2.4×）**，输出经四层验证与串行逐位一致
  （改前参考哈希复现 + 串并等价测试 + 47/47 独立核验 + 全量锁定）。
- `tests/test_v19_parallel.py`（串并等价 3 项）；答辩问答复赛增量备询卡 Q11–Q16；
  `docs/adr/adr-0006-async-jobs-in-memory.md`（异步作业内存态语义）。
- 本地仓库接入 GitHub 私有远程（快照分支方案，详见使用指南第四节）。

### Fixed
- **并行暴露的缓存线程安全缺陷**：进程级缓存淘汰复合操作 `pop(next(iter()))`
  非原子，并行场景高频插入下两线程可同时淘汰同一键（KeyError）；4 处
  （material option_id、ledger 因子解析/净减排/成本账本）统一持锁修复，
  串行行为不变。e2e_v12 两连挂按 S3 纪律定位。

## [v18] — 2026-09-14 · 大案例可用性 · 界面与容错轮

输入 `docs/优化提示词-SPEC-v18.md`；**用户执行前追加「界面美化」与「容错性」两任务**
（记为任务 1/2，SPEC 原任务顺延）。记录 `docs/自审-v18.md`。验收：WSL pytest
**255/255**（0 跳过）· 独立穷举核验 **47/47** · 用词门禁 4/4 · E2E **18/18 + 31/31 +
17/17 + 9/9**（新增 e2e_v18 容错套件）· 发行 VERSION=v18（demo 55 / pilot 160 文件），
verify_release **14/14**。任务 5（因子影子试算）门控未过（数据源连续两轮实检失败），
按纪律跳过。

> v18 节标题曾在 v17 节编写时被编辑事故吞没（CHANGELOG 编辑纪律教训，见
> docs/自审-v19.md 遗留项）；v19 终验恢复时标题误置于正文之后，v22 复位为
> 「标题→引言→正文」结构。

### Added
- **大案例异步运行路径**：`POST /api/tasks/{id}/runs?async=1`（202 + job_id，受理即返）
  + `GET /api/runs/jobs/{job_id}` 状态轮询（queued/running/done/failed + 已用时）；
  单作业守卫（第二个后台作业 409）；作业为进程内存态、重启即失（如实告知）；
  同步与异步共用 `_execute_run`，run 结果逐位一致（红线 3，单测把守）；audit
  `run.async`。前端供给 ≥16 自动走异步，按钮实时显示已用时。
- **工作台容错性**：网络断开友好提示、会话过期统一回登录页、登录行内提示不弹窗、
  撮合计算/建议单导出防重复提交、供给删除二次确认；`tests/e2e_v18.py` 真实
  Chrome 9 项容错 E2E。
- **完整分析耗时结构测量**：`benchmarks/analyze_time_structure.py` +
  `docs/完整分析耗时结构-v18.md`。结论：敏感性核查（6 场景 × 每场景重枚举）占
  47% 为最大单项，N-1 可忽略；前沿求解仅 5%——「求解基准 19.2s vs 端到端 809s」
  的 40 倍差异拆解完毕；提速方向评估（场景级并行化为首推，输出逐位一致可保持，
  本轮不动手）。
- `docs/自审-v18.md`。

### Changed
- **工作台界面美化**：style.css 系统化升级（品牌毛玻璃 sticky 头部、卡片/按钮/表格/
  表单/徽标/空态/视图过渡/滚动条；数字等宽；补 SVG 附带文本样式）；零 DOM 改动，
  冻结数值逐字符不变；11 号素材随新外观重摄。
- 商用试点手册 §7 增「后台计算」口径；发行白名单同步 v18 全部新文件；VERSION 升 v18。

### Fixed
- runEngine 重构时丢失同步分支 POST 的回归（e2e_round2 连续两次同点失败按 S3 纪律
  转缺陷排查定位；修复后四套 E2E 全绿）。

## [v17] — 2026-09-14 · 复赛就绪与试点交付轮

输入 `docs/优化提示词-SPEC-v17.md`，记录 `docs/自审-v17.md`。验收：WSL pytest
**248/248**（约 107s，0 跳过）· 独立穷举核验 **47/47** · 用词门禁 4/4 · E2E
**18/18 + 31/31 + 17/17**（e2e_v16 由 14 增至 17，既有 14 项保持）· 发行 VERSION=v17
（demo 55 / pilot 152 文件），verify_release **14/14**。任务 4（因子影子试算）双重
门控未过（用户未确认 + openLCA EcoProfiles 连续两轮实检失败），按纪律跳过并如实记录。

### Added
- **撮合建议单导出（试点交付物 v1）**：`POST /api/matching/sheet`（与 preview 共享
  `_matching_payload` 计算、audit 参数化）+ `src/matching_sheet.py` 纯函数渲染
  （确定性拼装、全转义、无外部资源、打印友好，含匹配明细/未满足原因/剩余容量/
  双边 SVG/标注+生成时间+入参快照）；工作台结果区「导出建议单」按钮（fetch→Blob
  下载，服务端不存副本，只读不落库口径不变）。`tests/test_v17_matching_sheet.py`
  8 项；e2e_v16 增至 17 项（新增下载断言 + accept_downloads）+ 证据文件
  matching-sheet-e2e.html。
- **答辩与演示素材升级**：`docs/六分钟预答辩脚本.md` v17 口径（完整前沿与三方法
  互验/撮合建议页 live demo/≤20 并发实测数字三段，各 30s+90s 双讲法；提交版
  -v10 不动）；`scripts/ppt_shots.py` v17 批次 `11_matching/12_load_benchmark/
  13_dashboard_charts`（缺省只产新批次，v12 十张移入 `--legacy` 旗标；压测表数字
  自动取自原始 JSON）；截图索引 v17 节；PPT与视频素材说明附录（复赛 13 页大纲
  建议 + 两套口径问答弹药）。
- **大案例压力实测**：`benchmarks/load_benchmark.py --fixture large`（30 供给与
  求解基准-v14 同源，rng 序列实检逐位一致）+ raw 数据 2 件 +
  `docs/压力基准-v16.md` 增补节。实测：30 供给单发 809.0s（≈13.5 分钟）、2 并发
  P95 1,191.7s、4 并发 P95 1,092.2s，全链路零错误——首次给出「试点跑大案例预期
  等待」端到端数字（此前仅纯前沿单段 19.2s）。
- `docs/adr/adr-0005-matching-preview-no-persistence.md`（撮合建议即算即返不落库，
  含升级触发条件）。
- `docs/项目现状与使用调试指南.md`（用户要求生成：现状快照 + 使用调试手册）。

### Changed
- `docs/商用试点手册-v10.md` §7 增加大案例预期等待口径；§10「大案例并发未覆盖」
  更新为已实测（更多并发不外推）。
- 发行白名单同步 v17 全部新文件；VERSION 升 v17。

### Fixed
- 撮合 API 抽取 `_matching_payload` 后早退路径返回 None 导致二次响应/500（两端点
  补 None 守卫，回归确认服务器线程零异常栈）；matching_sheet 渲染 append 误传
  双参（单测拦截）。


## [v16] — 2026-09-11 · 试点就绪与平台雏形轮

输入 `docs/优化提示词-SPEC-v16.md`，记录 `docs/自审-v16.md`。验收：WSL pytest
**240/240**（约 68s，0 跳过）· 独立穷举核验 **47/47** · E2E **18/18 + 31/31 + 14/14**
· 发行 VERSION=v16，verify_release **14/14**。任务 4（因子影子试算）为用户门控任务，
本轮未获确认而跳过，不阻塞验收。

### Added
- `CHANGELOG.md`（本文件）：手工回填 v13→v15 三轮；提交信息规范正式写入尾部说明。
- **撮合能力上工作台**：`POST /api/matching/preview` 只读试算 API（viewer 403/CSRF/
  入参校验/确定性/不落库仅 audit；批次池跨任务去重、容量=实时全局可用量、引擎输出
  透传+逐需求撮合理由）；工作台「多需求撮合」页（多选任务 → 汇总/匹配明细/未满足
  原因/批次剩余容量/稳定性自检徽标）；双边关系 SVG（`src/visuals.py` 新增
  `matching_svg`：左=需求右=批次、连线宽度=分配量，借鉴 OpenSupplyChains/Manifest
  关系视图思想，零依赖确定性）。页面三处明示「撮合建议（非交易/非预留），不改变
  批次库存真相」。
- `tests/test_v16_matching_api.py`（9 项：权限/CSRF/校验/确定性/不超卖/去重/不落库/
  audit）；`tests/e2e_v16.py`（真实 Chrome 14 项，console 零错误）；
  `docs/自审第二轮-v16.md`（活文档）与 `docs/e2e-shots/matching-v16.png`。
- **服务器压力基准**：`benchmarks/load_benchmark.py`（零依赖 stdlib 并发爬坡 1/5/10/
  20/30 × 固定行为序列，方法学借鉴 Locust 未引入依赖）+ WSL/Windows 双口径原始数据
  8 件 + `docs/压力基准-v16.md`。实测结论：shared 口径全档零错误，20 并发引擎
  P95 0.76–1.80s，「<20 并发」声明成立且有裕量；per-user 口径 ≥10 并发登录触 429
  为限流设计内行为。
- `docs/adr/` 四篇种子 ADR（SQLite 单文件、求解器双路径互验、区间不进护照、
  ruff 只评估不引入）。

### Changed
- README「文档地图」节增加指向本文件的一行。
- `docs/商用试点手册-v10.md` §7 容量声明更新为实测口径（≤20 并发零错误 + 登录限流
  条目）；§10 移除「未做压力基准」陈旧表述。
- 发行白名单补入 v16 全部新文件；VERSION 升 v16。
- 任务列表 API 增补 `supply_count` 字段（只增不改）。

### Fixed
- **发行白名单补入 `src/matching.py`**（v14 起漏加：包内 test_v14_matching.py 会因
  缺文件失败，v16 撮合 API 会 500——与 v15 漏加 src/visuals.py 同族，本轮终验前
  实测拦截）。

## [v15] — 2026-09-10 · 结构与呈现轮（tag `v15-final`）

验收：WSL pytest **231/231**（81.33s，0 跳过）· 独立穷举核验 **47/47** · E2E **18/18 + 31/31**
· 发行 VERSION=v15，verify_release **14/14**。输入 `docs/优化提示词-SPEC-v15.md`，记录
`docs/自审-v15.md`。

### Added
- `src/visuals.py`：零依赖内联 SVG 可视化（纯函数、确定性、无外部资源）——
  `pareto_scatter_svg`（气泡=订单最大份额、颜色=方案四类分类、基准成本参考线、非支配前沿
  虚线；保留 `data-option-id` 悬停联动）、`n1_service_svg`（各代表方案最差交付服务率条形）、
  `interval_svg`（v14 净减排区间首次可视 + 等价数据表 + 「非统计置信区间」注记；demo 无区间
  自动隐藏）。落点：站点第 4/5/6 节，仪表盘新增「图表总览」节。
- `tests/test_v15_visual.py`（10 项）：图表存在性、demo 区间隐藏、冻结数值逐字符不变、
  确定性（剥离护照时间戳后字节一致）、带区间因子、helper 边界。
- `docs/INDEX.md` 文档地图（四区：当前口径 / 活文档 / 试点与答辩操作 / 历史归档）；
  `docs/仓库结构盘点-v15.md`（40 个 md 逐文件三分类 + `grep -rln` 引用实测）。
- `run.py --stamp YYYYMMDD`（输出批次日期参数化，缺省当天）。

### Changed
- docs 分层归档 11 件 → `docs/archive/`（`git mv` 保留历史：实施记录、实施跟踪-v10/v11、
  自审第一轮-v10/v11/v12、自审第二轮-v11、最终自主验收-v12、Codex交接-v12、
  独立审查发现-第一轮-v10、材料事实修订清单-v10）；引用逐项同步 7 处（发行白名单 / README /
  PPT与视频素材说明-v12 / 答辩技术问题 / SPEC-v14·v15 / 六分钟预答辩脚本-v10 /
  test_docs_wording 注释），移动后全仓复扫零残留。
- `运行市场供需分析.bat`（硬编码旧项目失效路径）→ `docs/archive/legacy-scripts/`；
  `output/再生PP*_20260903.*` 4 件 → `output/archive/`。
- `scripts/build_release.py`：演示包报告/仪表盘改为 output 最新批次自动取件
  （`_latest_outputs()`），消除固定日期失配；VERSION 升 v15。
- README 新增「文档地图」节。

### Removed
- `cron/` 空目录（用户确认删除）。

### Fixed
- `src/visuals.py` 补入发行白名单（v15 终版；漏加曾致 verify 翻车——新模块必须同步白名单）。
- `src/material.py` 文档字符串转义修复。

## [v14] — 2026-09-08 · 能力跃迁轮（tag `v14-final`）

验收：WSL pytest **221/221**（64.15s）· 独立核验 **47/47** · E2E **18/18 + 31/31** ·
发行 VERSION=v14，verify_release **14/14**。输入 `docs/优化提示词-SPEC-v14.md`，记录
`docs/自审-v14.md`。

### Added
- **git 版本管理落地**：`.gitignore` 收敛（1456 → 437 项入库，排除 .e2e-venv/dist/
  pilot/data/pilot/backups/.pytest_cache）；v13 终态基线 commit + tag `v13-final`；此后每任务
  一 commit。未配置远端、未 push。
- **净减排区间传播**（确定性 UQ）：`src/ledger.py` 新增 `net_interval`（因子 [value_min,
  value_max] 极值传播）；`src/material.py` 因子校验支持可选区间对（成对、0 ≤ min ≤ value ≤ max）；
  `build_options` 在因子带区间时输出 `net_interval_kgco2e` + 「非统计置信区间」注记。
  `tests/test_v14_interval.py`（10 项）。
- **任意规模完整非支配前沿**：`_solver_mix` 从三锚点升级为完整三维前沿（阶段分解 +
  直接后继搜索 + 最小 max-k 收尾；三锚点全局精确先解；`FRONTIER_CAP=500` 触达如实截断
  `is_complete=False`）。`tests/test_v14_frontier.py`（12 项：≥10 组随机案例与独立穷举前沿
  集合相等、CAP 诚实截断、锚点恒在）。基准 `benchmarks/solver_benchmark_v14.py` →
  `benchmarks/求解基准-v14.md`（12 供给 4.8×10¹⁰ 组合完整前沿 427 点 5.5s；16/20/30 供给
  触 CAP 7.4s/11.5s/19.2s）。
- **双边撮合引擎 v0**：`src/matching.py`（Gale-Shapley 容量变体 / hospital-residents；
  需求侧偏好=到厂单价↑、净减排强度↓、距离↑；批次侧偏好=毛利空间↓【演示模型，如实标注】、
  需求量/产能↑；可行集复用 `match_gates`；输出 matches / unmatched（带原因）/ 剩余容量 /
  稳定性自检——阻断对恒为零）。仅撮合建议，不产生预留/交易。
  `tests/test_v14_matching.py`（17 项）。

### Changed
- README 能力段落按 v14 如实更新（完整前沿路径与边界、区间传播、撮合引擎 v0 口径）。
- 求解耗时从 v13 的毫秒级（锚点路径）升为秒级（完整前沿）——以耗时换完整性，文内注明。

### Fixed
- `solve_lex` 返回元组序错误（末级不一定是成本，(net, cost) 颠倒致后继/收尾求解失配）→ 按语义取键。
- 二分中点分割不完备（中点约束误用右端 net、「左箱为空即丢弃右半」漏点；种子 12 案例
  真值 12 点仅得 5 点）→ 重写为直接后继搜索，种子 50 真值 12 点全部复现。
- CAP 截断后全局成本锚点可能位于未扫描阶段而被贪心反超（74,870.95 vs 73,513.55）→
  三锚点全局先解，截断后锚点仍精确。

## [v13] — 2026-09-07/08 · 引擎与数据层升级轮（tag `v13-final`）

验收：WSL pytest **182/182**（82.62s）· 独立核验 **47/47** · E2E **18/18 + 31/31** ·
发行 VERSION=v13（competition-demo 51 文件 / pilot-app 107 文件），verify_release **14/14**
（首次覆盖 ortools 全新 venv 安装链路）。本轮先于 git 落地完成（git 历史始于 v13 终态基线），
原 SPEC 提示词未单独入库；记录见 `docs/自审-v13.md`。

### Added
- **OR-Tools CP-SAT 求解路径 + 穷举互验**：`src/material.py` 新增 `_solver_mix`；
  `simulate_supply_mix` 超限分支先尝试 CP-SAT（×10⁶ 定点整数化、求解后用与穷举相同的
  `_mix_plan` 复算真实账本并校验量化偏差），未安装/不可行/未证实最优/校验未过 → 回退既有贪心
  路径（v12 行为逐键保留）。`tests/test_v13_solver.py`（23 项：≥10 组随机案例成本/碳/分配
  矩阵/option_id 逐位一致、前沿支配反证、未安装回退契约、确定性、量化无损性、演示案例冻结值）。
  基准 `benchmarks/solver_benchmark.py` → `benchmarks/求解基准-v13.md`。
  WSL 安装 OR-Tools **9.15.6755**；`requirements.txt` 记录 `ortools>=9.0`。
- **数据模型 v5**（SCHEMA_VERSION 4→5，纯增量）：`supplies`/`batches` 增加可选列
  `external_id`（外部权威编号）；新增 `relations` 三元组表（UNIQUE 幂等）与
  `add_relation`/`list_relations`；CSV/API 透传该列。`tests/test_v13_datamodel.py`（7 项，
  含 v4 老库升级字节保留与回滚演练）。ER 与迁移说明 `docs/数据模型-v13.md`。
- **召回阈值门控 + 兜底**：`src/material_match.py` G1 高相似通道（≥0.45）/ G2 类别联合通道 /
  兜底层 tier=fallback（可见、靠后、不进指标）/ tier=out；`tests/test_v13_recall_gating.py`
  （5 项）+ `benchmarks/material_matching_cases_v2.csv` 新增 CASE-18/19/20 holdout。
  dev Recall@1 0.7、holdout 0.5、误放行 0；跨材料负例误召回 ABS 4→1、PE 4→2。
- `docs/因子库接入方案-v13.md`：10 个候选因子源逐项实访（许可/商用/覆盖/更新/获取），
  关键发现 openLCA EcoProfiles for Recycled Plastics（CC BY 4.0，含 rPP 颗粒 gate-to-gate）。

### Changed
- README 基准段落废止 v10 前「6 案例 Recall@1=1.0」单候选口径，按 v13 实测更新。
- `tests/test_docs_wording.py` 为用户暂存区 `清华绿创赛/` 增加窄域排除（用户文件未动）。

### Fixed
- 进程级缓存 id 复用误命中：`option_id` / `net_emissions_ledger` / `project_cost_ledger`
  三处缓存键含 `id(对象)` 但不持有引用（v12 性能优化潜伏缺陷）→ 缓存值一律携带被键引用对象。
- `Database.__init__` 用 `INSERT OR IGNORE` 写 `meta.schema_version` 致老库版本号永远停留
  → 迁移成功后 UPSERT 盖章当前版本。
- 贪心回退组合选项缺 `_cost_full` 等全精度字段，未安装 ortools 的超限任务 `greedy_report`
  `None` 与 `float` 比较崩溃 → 补齐字段并「全精度优先、None 退展示值」；`site_builder` KPI
  对 `feasible_count=None` 如实渲染「—」。
- 召回规则 `matched_synonyms` 无条件命中分支（含材料代号即召回，不看文本）移除。
- `scripts/verify_release.py` 硬编码原项目 venv 路径已不存在 → 路径存在性回退当前解释器。

## 基线 [v12] — 2026-09-06（git 之前，见 `docs/archive/Codex交接-v12.md`）

147/147 测试 · E2E 18/18 + 31/31 · verify 14/14 · 发行 v12；赛事提交材料（1000 字介绍、
12 页 PPT、视频、报名 zip、`docs/ppt_assets/` 十张截图）自此冻结，永不回改。

---

## 提交信息规范（自 v14 沿用，v16 起正式约定）

- **首行格式**：`vXX任务N：<变更内容>`（全角冒号；多任务合并提交写 `vXX任务N+M：`；
  轮次终验写 `vXX 终版：<验收数字>`；SPEC 落盘写 `SPEC-vXX：<主题>`）。
- **首行内容**：说清「做了什么」而非「改了哪个文件」；含关键数字（测试数、锚点、基准）时直接
  写入，便于 `git log --oneline` 即可追溯验收状态。
- **粒度**：每任务一 commit、独立可回退；SPEC 本身单独 commit。
- **不做**：不配置远端、不 push（红线 5：外部发布需用户确认）；不 `--amend` 已验收的提交。
- **每轮收尾**：终验 commit 后打 tag `vXX-final`；同时在本文件把 `[Unreleased]` 改为正式版本节。
