// 手册内容 B：第 19–36 章
module.exports = [
{ h1: "第 19 章 如何新增页面" },
{ p: "工作台的「页面」= app.js 中的视图区块（section id=view-xxx）+ 视图切换函数，由单一 workbench.html 承载，无前端路由。新增步骤：① workbench.html 增加区块与入口按钮；② app.js 增加视图渲染函数并接入切换；③ style.css 用既有令牌补样式；④ 需要数据则按第 20 章新增 API。" },
{ p: "参考样例：多需求撮合视图（view-matching）是最佳的「独立页面式」参考——入口按钮在任务列表、返回按钮、独立渲染函数齐备。命名建议 view-功能名；修改后必须跑四套 E2E（console 零错误门禁）。" },

{ h1: "第 20 章 如何新增后端 API" },
{ p: "本项目后端为标准库 http.server，无装饰器路由。新增 API 的固定模式：" },
{ code: "1. server.py do_GET（读）或 do_POST（写）分支中加：\n   if path == \"/api/your-feature\" and method == \"POST\":\n       self._require(session, \"editor\")   # 鉴权\n       self._api_your_feature(session)\n2. 实现 _api_your_feature(session)：\n   body = self._read_body()               # 大小限制 1MB\n   # 参数校验：非法抛 ValidationError → 自动 400+details\n   # 业务：调 src/ 或 db.execute（参数化 SQL）\n   self._json({...})                      # 统一返回（自动附安全头/no-store）\n3. tests/ 新增用例（鉴权/CSRF/边界/幂等）" },
{ p: "约束：返回结构与错误结构保持一致；写操作需要 CSRF 头校验（框架已做，勿绕过）；新文件登记发行白名单。" },

{ h1: "第 21 章 如何修改数据库" },
{ p: "迁移机制：pilot/db.py 内维护版本化迁移函数（v1→v2→…→v5，全部幂等），启动时自动执行到最新版本并盖章 schema 版本。新增字段/表的步骤：" },
{ code: "1. db.py 增加迁移函数 v5→v6（ALTER TABLE ... / CREATE TABLE，幂等写法）\n2. 迁移登记进 _migrate 链\n3. 同步修改：pilot/api.py（领域对象）→ server.py（读写列）\n   → app.js（展示/提交字段）→ tests（断言）\n4. 老库兼容：迁移须能从任意历史版本一步到位；删除列用「不再读写」而非物理删\n5. 手工检查：sqlite3 pilot/data/pilot.sqlite3 \".schema 表名\"" },
{ p: "注意：删除字段推荐停用不物理删；关键表 runs/selections/reservations/audit_log 的历史数据不可清理；改表后必须完整回归（含备份恢复往返）。" },

{ h1: "第 22 章 如何修改项目配置" },
{ tbl: { head: ["配置", "文件", "说明"], w: [24, 38, 38], rows: [
  ["排放因子/核算约定", "config/material_emission_factors.yaml", "factor 角色/值/区间/边界关键词；改约定须建新基准版本"],
  ["行业与需求模板", "config/industries.yaml", "run.py --list-industries 可列出"],
  ["证据动作", "config/evidence_actions.yaml", "证据价值排序的动作定义"],
  ["服务端口", "start_pilot.py/serve.py 的 --port", "默认 8765"],
  ["并行线程", "环境变量 SUPPLYLENS_PARALLEL_WORKERS", "0=自动；1=强制串行"],
  ["求解调试", "环境变量 MATERIAL_SOLVER_DEBUG=1", "输出求解过程"],
]} },

{ h1: "第 23 章 日常继续开发流程" },
{ code: "开始开发：\n  cd 仓库根目录 → git pull（如有远端）→ python --version\n  启动工作台（8765）→ 打开首页 → 登录确认基础功能\n修改功能：\n  先查第 12/13 章调用链 → 最小修改 → 不盲改多个模块\n修改完成：\n  python -m compileall -q src pilot（语法）\n  python -m pytest tests -q                # 271 全绿\n  python -m pytest tests/test_docs_wording.py -q\n  浏览器实测 + Console + Network\n  （动浏览器层）四套 E2E\n  更新 CHANGELOG.md" },

{ h1: "第 24 章 Debug 总体方法" },
{ p: "出现问题时先定位层级，不要直接改代码。判断顺序：" },
{ code: "用户操作\n ↓\n浏览器页面（渲染对不对）\n ↓\nConsole（JS 报错）\n ↓\nNetwork（请求发出没有/状态码/响应体）\n ↓\n后端日志（服务控制台的 done POST /api/... in Xms 与异常栈）\n ↓\nserver.py 路由分支 → _api_* 处理器\n ↓\nsrc/ 引擎或 pilot/db.py\n ↓\nSQLite 数据" },

{ h1: "第 25 章 前端 Debug" },
{ p: "Chrome 按 F12：Console 面板看 Error/TypeError/undefined/Promise rejection（本项目 E2E 门禁即 console 零错误，正常状态不应有红色报错）；Network 面板筛 Fetch/XHR，逐项检查 Request URL、Method、Status、Payload、Response。" },
{ p: "本项目优先检查文件：pilot/static/app.js（对应视图函数的取数与渲染）、workbench.html（元素 id 是否存在）、style.css（视觉异常）。注意 401 会话过期会自动回登录页，属设计行为；403 为角色不足。" },

{ h1: "第 26 章 后端 Debug" },
{ p: "VS Code：配置 Python 调试器直接启动 pilot/start_pilot.py（或 attach 到运行进程），可设断点、单步、看 Variables 与 Call Stack。PyCharm 同理。" },
{ p: "推荐断点位置：server.py do_POST 路由分支第一行（确认命中哪个 _api_*）；_api_run_engine 冻结快照前后（看输入与因子内容）；src/material.py 门槛判定与前沿求解入口；db.execute 前看最终 SQL 参数；_error 调用处看失败原因。" },
{ p: "特殊技巧：pytest 捕获会吞掉服务线程的异常栈，排查 500 时用「python -m pytest tests/xxx -q -s 2>err.log」完整保留栈（见使用指南第五节）。" },

{ h1: "第 27 章 API Debug" },
{ tbl: { head: ["现象", "本项目优先检查"], w: [30, 70], rows: [
  ["请求完全没发出", "app.js 对应函数是否执行（视图切换/按钮禁用态/防重复提交）"],
  ["400", "响应 details 字段逐条看；对应 api.py/validation.py 校验规则"],
  ["401", "会话过期（12 小时）或未登录；前端已自动回登录页则重新登录"],
  ["403", "viewer 角色调写接口（设计行为）；或静态资源不在白名单"],
  ["404", "URL 拼写/任务 id 不存在；对照第 14 章路由表"],
  ["429", "登录限流（900 秒 10 次/每 IP），等待窗口或换网络"],
  ["409", "异步单作业冲突：已有后台作业未完成"],
  ["500", "服务控制台必有 log.exception 异常栈；常见为数据库错误或处理器早退返回 None"],
  ["200 但页面不对", "看响应 JSON 字段与 app.js 取值路径是否一致"],
]} },

{ h1: "第 28 章 数据库 Debug" },
{ p: "SQLite 无需检查服务进程，直接验证文件与内容：" },
{ code: "sqlite3 pilot/data/pilot.sqlite3\nsqlite> .tables                              -- 表是否齐全\nsqlite> PRAGMA table_info(users);            -- 字段结构\nsqlite> SELECT * FROM meta;                  -- schema 版本\nsqlite> SELECT count(*) FROM runs;           -- 数据是否存在\n\n# 也可以不走 SQL：GET /api/health 返回 users_exist/schema_version\n# 备份恢复类问题：恢复前会自动 WAL checkpoint；Windows 下临时目录\n# 删除偶发 PermissionError 属文件锁滞后，脚本已重试兜底" },

{ h1: "第 29 章 常见错误及解决方法" },
{ tbl: { head: ["错误", "常见原因", "如何判断", "解决"], w: [24, 26, 22, 28], rows: [
  ["端口 8765 被占用", "旧进程未退", "启动即报 OSError", "结束旧进程或换 --port"],
  ["登录 429", "同 IP 900 秒 10 次限流", "响应 429", "等 15 分钟或换网络；勿调宽参数"],
  ["初始化 400 需字母与数字", "口令策略（v27 起）", "响应 error 文案", "改用含字母与数字的口令"],
  ["ModuleNotFoundError: yaml", "未装 PyYAML", "import 即抛", "pip install -r requirements.txt"],
  ["引擎明显偏慢/结果标注回退", "未装 ortools", "结果 notes/环境说明", "pip install ortools（可选）"],
  ["Windows 临时目录 PermissionError", "SQLite 文件锁滞后", "删除临时目录时偶发", "忽略，脚本已重试兜底"],
  ["静态资源 403", "不在白名单", "访问 /static/ 以外路径", "只用 workbench/app.js/style.css"],
  ["CSV 导入 400", "行级体检失败", "响应 details 逐行列出", "按 details 修正 CSV 后重导"],
  ["WSL 里 cd 中文目录失败", "编码/挂载层", "直接 cd 报路径不存在", "用通配符 /mnt/<盘>/<路径>/*透镜*"],
  ["E2E 偶发 RST/超时", "Windows 网络层抖动", "同点首次失败", "原样重跑一次；连续两次同点失败才查代码"],
  ["页面全白/样式丢失", "直接以 file:// 打开工作台", "地址栏协议", "必须经 http://127.0.0.1:8765 访问"],
  ["测试数与文档不一致", "文档滞后", "以 pytest 实际输出为准", "文档口径为 271，以实际运行为准"],
]} },

{ h1: "第 30 章 项目测试方法" },
{ h2: "30.1 测试功能：登录与角色" },
{ p: "前置条件：全新数据库（临时目录）已启动工作台。步骤：① 初始化管理员（强口令）；② 登录；③ 用 viewer 帐号调任意写接口。预期：①200；②200 并返回 csrf；③403。API 确认：/api/auth/me 返回角色。失败先查：users 表是否已存在账号、口令策略。" },
{ h2: "30.2 测试功能：导入供给并运行引擎" },
{ p: "前置：已登录 editor，任务已建。步骤：模板 CSV 填两行 → POST supplies/import → 运行计算（同步）→ GET passport。预期：导入 201/200 且体检零错误；运行返回 run_id；护照为 Schema v2 且绑定 run_id。失败先查：CSV details、因子表是否含对应 material_code、冻结锚点数值是否被改动。" },
{ h2: "30.3 测试功能：多需求撮合" },
{ p: "前置：两个任务各有供给。步骤：撮合页勾选两任务 → 计算 → 导出建议单。预期：汇总含阻断对=0 徽标；建议单 HTML 与页面数值同源；不产生任何预留（批次可用量不变）。失败先查：/api/matching/preview 响应、matching.py 输入任务是否同物料。" },
{ p: "自动化测试入口：python -m pytest tests -q（271 项）；E2E：tests/e2e_v16.py、e2e_v12.py、e2e_round2.py、e2e_v18.py（Windows .e2e-venv + Chrome）。" },

{ h1: "第 31 章 修改后的回归测试" },
{ code: "□ python -m compileall -q src pilot scripts        语法通过\n□ python -m pytest tests -q                        271/271，0 跳过\n□ python docs/独立核验脚本-v10.py                   47/47\n□ python -m pytest tests/test_docs_wording.py -q    4/4\n□ （动浏览器层）四套 E2E 17+18+31+9，console 零错误\n□ （涉发行）build_release + verify_release 14/14\n□ （涉演示层）demo_selfcheck 9/9\n□ 冻结锚点 83,025 / 64,182 / 21,072.98 / 59,941.25 逐字符不变\n□ CHANGELOG.md 已更新" },

{ h1: "第 32 章 项目维护注意事项" },
{ tbl: { head: ["类别", "内容"], w: [24, 76], rows: [
  ["不可随意改", "src/material.py、ledger.py、matching.py（引擎改动必须逐位一致或等价论证+47 组核验）；活文档（INDEX 活文档表的文件被脚本重写，勿改名）"],
  ["不可随便升级", "运行依赖保持零新增；ortools 为可选加速；不引入 lint/框架（ADR-0004）"],
  ["敏感配置", "无密钥类配置；pilot/data/ 与 pilot/backups/ 含真实业务数据，严禁入库或外传"],
  ["公共模块", "src/validation.py、pilot/api.py、pilot/auth.py 被多条链共用，改动需全链回归"],
  ["高影响模块", "pilot/server.py（全部 API）、app.js（全部前端）"],
  ["关键表", "runs（不可复算的快照）、reservations（库存真相）、audit_log（审计）"],
  ["多处使用的 API", "/api/health（自检/部署脚本）、/api/auth/*（全部页面）"],
]} },

{ h1: "第 33 章 给 AI 的「修改现有功能」提示词" },
{ code: "我要继续修改当前项目。\n\n本次需求：\n【填写需求】\n\n请先阅读项目中和这个功能有关的现有代码。\n不要直接重写整个项目。\n请先告诉我：\n1. 当前功能是怎么运行的。\n2. 用户操作入口在哪里。\n3. 前端涉及哪些文件。\n4. API 涉及哪些文件。\n5. 后端涉及哪些文件。\n6. 数据库涉及哪些表。\n7. 当前完整调用链是什么。\n8. 修改后可能影响哪些功能。\n\n然后生成修改计划。\n修改时每次按照以下格式：\n【文件路径】【当前代码作用】【需要修改的位置】\n【修改前代码】【修改后代码】【为什么这样修改】\n【是否影响其他模块】【是否需要重启】【如何验证】\n\n如果需要修改多个文件，请按照合理顺序逐个修改。\n不要大规模重构。\n完成以后告诉我：\n1. 需要运行什么命令。2. 是否需要重新安装依赖。\n3. 是否需要执行 migration。4. 是否需要重启前端。\n5. 是否需要重启后端。6. 如何测试功能。\n7. 正确结果是什么。8. 如果失败首先检查哪里。" },

{ h1: "第 34 章 给 AI 的「新增功能」提示词" },
{ code: "我要在当前项目中新增一个功能：\n【填写功能需求】\n\n请先扫描项目中最相似的现有功能。\n然后分析它的：页面/组件/路由/前端 API/\n后端处理器/校验/数据库/权限。\n请沿用当前项目已有架构实现（标准库 http.server + 原生前端 + SQLite）。\n不要创建第二套架构，不要为了一个简单功能增加大型依赖，不要随意升级框架。\n\n首先给出开发计划。\n然后按照项目最合理的顺序开发：\n数据库 → 后端处理 → 校验 → API → 前端 → 权限 → 测试\n\n每一步必须告诉我：修改哪个真实文件、为什么修改、代码放在哪里、\n是否影响其他模块、修改以后怎么验证。\n完成后执行完整回归测试（第 31 章清单）。" },

{ h1: "第 35 章 给 AI 的 Bug Debug 提示词" },
{ code: "当前项目出现 Bug。\n\n我的操作步骤：\n1. \n2. \n3. \n\n我执行的命令：【填写】\n完整报错：【粘贴完整报错】\n预期结果：【填写】\n实际结果：【填写】\n\n请不要靠猜测直接修改大量代码。\n请按照以下步骤：\n1. 判断问题属于：环境/依赖/配置/前端/API/后端/数据库/权限\n2. 找到第一个真正的错误。\n3. 定位具体文件（参考手册第 24–28 章分层）。\n4. 定位代码位置。\n5. 判断 root cause。\n6. 告诉我如何验证这个判断。\n7. 给出最小修改方案。\n8. 明确修改哪个文件。\n9. 给出具体修改内容。\n10. 告诉我修改以后执行什么命令。\n11. 告诉我如何确认已经修复。\n\n如果暂时不能确定，请用 Console/Network/后端日志/断点/数据库查询\n逐步缩小范围。不要同时乱改多个模块。" },

{ h1: "第 36 章 给 AI 的「解释代码」提示词" },
{ code: "请帮我解释下面这个文件或者函数。\n\n文件：【文件路径】\n\n请按照以下结构解释：\n【一句话作用】这段代码主要做什么。\n【谁调用它】上游调用者。\n【它调用谁】下游依赖。\n【输入】输入参数。\n【处理过程】按执行顺序解释。\n【输出】返回什么。\n【涉及数据库】涉及哪些表。\n【涉及 API】涉及哪些接口。\n【完整数据流】用箭头表示。\n【如果我要修改】告诉我哪些地方需要同步检查。\n\n请使用容易理解的方式，不要只解释语法。" },
];
