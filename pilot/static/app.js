/* 供需透镜试点工作台（vanilla JS，无外部依赖）。
   原则：业务数据只在服务端；localStorage 仅存界面偏好；
   输出全部转义；写入接口带 CSRF；失败状态如实显示，不伪造保存成功。 */
(function () {
  "use strict";
  var API = {
    csrf: localStorage.getItem("pilot.csrf") || "",
    onExpired: null,  // 会话过期钩子（登录后由界面层设置；返回 true 表示已处理）
    async request(method, path, body) {
      var opts = { method: method, credentials: "same-origin",
        headers: { "Content-Type": "application/json" } };
      if (this.csrf) opts.headers["X-CSRF-Token"] = this.csrf;
      if (body !== undefined) opts.body = JSON.stringify(body);
      var res;
      try {
        res = await fetch("/api" + path, opts);
      } catch (e) {
        throw new Error("无法连接服务器：请确认服务已启动、网络未断开"
          + "（业务数据只保存在本机，不会因刷新丢失）。");
      }
      if (res.status === 401 && this.onExpired && this.onExpired()) {
        throw new Error("登录已过期，请重新登录。");
      }
      var data = null;
      try { data = await res.json(); } catch (e) { /* 非 JSON */ }
      if (!res.ok) throw new Error((data && data.error) || ("请求失败 " + res.status));
      return data;
    },
    get(path) { return this.request("GET", path); },
    post(path, body) { return this.request("POST", path, body || {}); },
    put(path, body) { return this.request("PUT", path, body || {}); },
    del(path) { return this.request("DELETE", path); },
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function q(id) { return document.getElementById(id); }
  function fmt(value, spec) {
    if (value === null || value === undefined || value === "") return "-";
    if (spec && typeof value === "number") {
      if (spec === "cny") return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
      if (spec === "t") return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
      if (spec === "pct") return (value * 100).toFixed(1) + "%";
      if (spec === "int") return Math.round(value).toLocaleString("zh-CN");
    }
    return esc(value);
  }

  var state = {
    me: null, tasks: [], task: null, tab: "demand",
    supplies: [], run: null, runs: [], optimistic: false,
    selectedIds: [], supplyFilter: "all", supplySort: { key: "supply_id", dir: 1 },
    stale: false,
    evidenceTasks: [], evidenceLoading: false, evidenceError: null, evidenceLoaded: false,
    evidenceVersions: [],
  };

  /* —— 视图切换 —— */
  function show(view) {
    ["auth", "tasks", "task", "matching"].forEach(function (v) {
      q("view-" + v).hidden = v !== view;
    });
  }

  /* —— 认证 —— */
  async function initAuth() {
    try {
      state.me = await API.get("/auth/me");
      API.onExpired = function () {  // 会话过期：统一回登录页（登录前自身的 401 不算）
        if (!state.me) return false;
        state.me = null;
        q("btn-logout").hidden = true;
        show("auth");
        q("auth-title").textContent = "登录";
        q("btn-auth-submit").textContent = "登录";
        q("btn-auth-submit").dataset.mode = "login";
        q("auth-hint").textContent = "登录已过期，请重新登录。";
        return true;
      };
      q("user-chip").textContent = state.me.username + "（" +
        ({ admin: "管理员", editor: "编辑", viewer: "只读" }[state.me.role] || state.me.role) + "）";
      q("btn-logout").hidden = false;
      show("tasks");
      await refreshTasks();
    } catch (e) {
      state.me = null;
      q("btn-logout").hidden = true;
      show("auth");
      var health = null;
      try { health = await API.get("/health"); } catch (e2) { }
      if (health && !health.users_exist) {
        q("auth-title").textContent = "初始化管理员（首次启动，无硬编码默认密码）";
        q("auth-hint").textContent = "系统尚无账号：设置管理员用户名与密码（≥8 位）。";
        q("btn-auth-submit").textContent = "初始化管理员";
        q("btn-auth-submit").dataset.mode = "init";
      } else {
        q("auth-title").textContent = "登录";
        q("auth-hint").textContent = "";
        q("btn-auth-submit").textContent = "登录";
        q("btn-auth-submit").dataset.mode = "login";
      }
    }
  }
  q("btn-auth-submit").addEventListener("click", async function () {
    var btn = q("btn-auth-submit");
    if (btn.disabled) return;  // 防重复提交（网络慢时连点）
    var mode = btn.dataset.mode || "login";
    var username = q("auth-username").value.trim();
    var password = q("auth-password").value;
    btn.disabled = true;
    btn.classList.add("busy");
    try {
      if (mode === "init") {
        await API.post("/auth/init", { username: username, password: password });
      }
      var res = await API.post("/auth/login", { username: username, password: password });
      API.csrf = res.csrf;
      localStorage.setItem("pilot.csrf", res.csrf);
      await initAuth();
    } catch (e) {
      q("auth-hint").textContent = "登录失败：" + e.message;  // 行内提示，不再弹窗打断
    } finally {
      btn.disabled = false;
      btn.classList.remove("busy");
    }
  });
  q("btn-logout").addEventListener("click", async function () {
    try { await API.post("/auth/logout"); } catch (e) { }
    localStorage.removeItem("pilot.csrf");
    initAuth();
  });

  /* —— 任务列表 —— */
  async function refreshTasks() {
    var data = await API.get("/tasks");
    state.tasks = data.tasks;
    var html = state.tasks.length ? "<div class='table-scroll'><table><thead><tr><th>任务</th><th>case_id</th>"
      + "<th>状态</th><th>需求核心</th><th>更新时间</th><th></th></tr></thead><tbody>"
      + state.tasks.map(function (t) {
        var d = t.demand;
        return "<tr><td><a href='#' data-task='" + esc(t.id) + "'>" + esc(t.id) + "</a></td>"
          + "<td>" + esc(t.case_id) + "</td><td>" + esc(t.status) + "</td>"
          + "<td>" + esc(d.target_material_code || "") + " " + fmt(d.required_mass_t, "t") + " t · "
          + esc(d.application || "") + "</td>"
          + "<td>" + esc(t.updated_at) + "</td>"
          + "<td><button data-task-open='" + esc(t.id) + "'>打开</button></td></tr>";
      }).join("") + "</tbody></table></div>"
      : "<p class='empty'>暂无任务。点击「新建替代任务」开始（应用主线第 1 步）。</p>";
    q("task-list").innerHTML = html;
    q("task-list").querySelectorAll("[data-task]").forEach(function (a) {
      a.addEventListener("click", function (e) { e.preventDefault(); openTask(a.dataset.task); });
    });
    q("task-list").querySelectorAll("[data-task-open]").forEach(function (b) {
      b.addEventListener("click", function () { openTask(b.dataset.taskOpen); });
    });
    renderAdminPanel();
  }
  q("btn-new-task").addEventListener("click", function () {
    q("view-tasks").hidden = true;
    state.task = null;
    state.tab = "demand";
    state.supplies = []; state.run = null; state.runs = [];
    show("task");
    q("task-title").textContent = "新建替代任务";
    renderDemandWizard({});
  });

  function openTask(taskId) {
    API.get("/tasks/" + encodeURIComponent(taskId)).then(function (data) {
      state.task = data.task;
      state.supplies = data.supplies;
      state.runs = data.runs || [];
      state.run = null;
      state.evidenceLoaded = false;
      state.evidenceTasks = [];
      state.evidenceError = null;
      q("task-title").textContent = "任务 " + data.task.id + "（" + esc(data.task.case_id) + "）";
      show("task");
      // P1-1：恢复最近一次运行（不显示「尚未计算」的假空态）
      if (state.runs.length) {
        loadRun(state.runs[0].id).then(function () { setTab("demand"); });
      } else {
        setTab("demand");
      }
    }).catch(function (e) { alert("打开任务失败：" + e.message); });
  }
  q("btn-back").addEventListener("click", function () {
    show("tasks");
    refreshTasks();
  });

  function loadRun(runId) {
    return API.get("/runs/" + encodeURIComponent(runId)).then(function (data) {
      state.run = { id: data.run.id, created_at: data.run.created_at,
        config_sha256: data.run.config_sha256, result: data.result,
        meta: { elapsed_ms: data.meta ? data.meta.elapsed_ms : null } };
      state.optimistic = false;
      state.selectedIds = [];
      return state.run;
    });
  }

  function setTab(tab) {
    state.tab = tab;
    q("task-tabs").querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    ["demand", "supplies", "evidence", "compare", "passport", "feedback"].forEach(function (t) {
      q("tab-" + t).hidden = t !== tab;
    });
    if (tab === "demand" && state.task) renderDemandWizard(state.task.demand_json ? JSON.parse(state.task.demand_json) : state.task.demand);
    if (tab === "demand" && !state.task) renderDemandWizard({});
    if (tab === "supplies") renderSupplies();
    if (tab === "evidence") { renderEvidence(); if (!state.evidenceLoaded && !state.evidenceLoading) loadEvidence(); }
    if (tab === "compare") renderCompare();
    if (tab === "passport") renderPassport();
    if (tab === "feedback") renderFeedback();
  }
  q("task-tabs").addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-tab]");
    if (btn) setTab(btn.dataset.tab);
  });

  /* —— 1 需求向导（可保存、可继续编辑） —— */
  function demandFields() {
    return [
      ["demand_id", "需求编号*", "text"], ["buyer_name", "需方名称", "text"],
      ["target_material_code", "目标材料代码*", "text"], ["application", "用途", "text"],
      ["required_mass_t", "需求量 (t)*", "number"],
      ["min_recycled_content_pct", "再生含量下限 (%)", "number"],
      ["mfi_min", "熔指下限 (g/10min)", "number"], ["mfi_max", "熔指上限 (g/10min)", "number"],
      ["max_moisture_pct", "水分上限 (%)", "number"], ["max_ash_pct", "灰分上限 (%)", "number"],
      ["accepted_form", "接受形态（、分隔）", "text"], ["accepted_color", "接受颜色（、分隔）", "text"],
      ["required_from", "需求开始 (YYYY-MM-DD)", "text"], ["due_date", "交付截止 (YYYY-MM-DD)", "text"],
      ["destination", "目的地", "text"],
      ["baseline_virgin_price_cny_per_t", "基准原生价 (CNY/t)", "number"],
      ["max_distance_km", "距离上限 (km)", "number"],
    ];
  }
  function renderDemandWizard(demand) {
    var fields = demandFields().map(function (f) {
      var value = demand[f[0]] !== undefined && demand[f[0]] !== null ? demand[f[0]] : "";
      return "<div><label><b>" + esc(f[1]) + "</b></label>"
        + "<input id='dw-" + esc(f[0]) + "' type='" + f[2] + "' value='" + esc(value) + "'"
        + (f[2] === "number" ? " step='any'" : "") + "></div>";
    }).join("");
    q("tab-demand").innerHTML = "<div class='card'><h3>需求向导（单位清晰，保存后可继续编辑）</h3>"
      + "<div class='field-row'>" + fields + "</div>"
      + "<div class='form-actions'><button class='primary' id='btn-save-demand'>保存需求</button>"
      + "<span class='empty' id='demand-save-msg'></span></div></div>";
    q("btn-save-demand").addEventListener("click", saveDemand);
  }
  async function saveDemand() {
    var demand = {};
    demandFields().forEach(function (f) {
      var raw = q("dw-" + f[0]).value.trim();
      demand[f[0]] = raw === "" ? null : raw;
    });
    // 数字字段转数值
    ["required_mass_t", "min_recycled_content_pct", "mfi_min", "mfi_max",
      "max_moisture_pct", "max_ash_pct", "baseline_virgin_price_cny_per_t", "max_distance_km"]
      .forEach(function (k) {
        if (demand[k] !== null) demand[k] = Number(demand[k]);
      });
    try {
      if (state.task) {
        await API.put("/tasks/" + encodeURIComponent(state.task.id), { demand: demand });
        state.stale = true;  // 需求修改后旧结果过期
        q("demand-save-msg").textContent = "已保存（旧结果已标为过期，请重新计算）。";
      } else {
        var res = await API.post("/tasks", { demand: demand });
        q("demand-save-msg").textContent = "已创建任务 " + res.task_id + "。";
        openTask(res.task_id);
      }
    } catch (e) { q("demand-save-msg").textContent = "保存失败：" + e.message; }
  }

  /* —— 2 供需与质量 —— */
  function supplyTemplateCsv() {
    return "supply_id,supplier_name,material_code,material_name_raw,polymer_type,form,color,grade," +
      "mfi_g_10min,moisture_pct,ash_pct,recycled_content_pct,available_mass_t,available_from," +
      "available_to,origin,price_cny_per_t,price_includes_freight,yield_pct,inspection_required," +
      "preprocessing,transport_mode,factor_id,distance_km,evidence_source,evidence_date," +
      "evidence_attachment,source\n" +
      "SUP-001,示例供应商,PP,再生PP颗粒（黑色）,PP,颗粒,黑色,注塑级,12,0.3,3,95,20," +
      "2026-09-10,2026-10-31,示例园区,4200,否,100,否,已造粒,公路货运,,30,检测报告,2026-08-15,,示例\n";
  }
  function renderSupplies() {
    if (!state.task) { q("tab-supplies").innerHTML = "<p class='empty'>先保存需求（第 1 步）再录入供给。</p>"; return; }
    var filterBtns = ["all", "eligible", "pending_evidence", "ineligible"].map(function (f) {
      var label = { all: "全部", eligible: "可比较", pending_evidence: "待核验", ineligible: "不符合" }[f];
      return "<button class='state-btn' data-state='" + f + "' aria-pressed='" + (state.supplyFilter === f) + "'>" + label + "</button>";
    }).join("");
    var matchMap = {};
    if (state.run) state.run.result.match_states.forEach(function (s) { matchMap[s.supply_id] = s; });
    var rows = state.supplies.map(function (s) {
      var m = matchMap[s.supply_id];
      var label = m ? ({ eligible: "可比较", pending_evidence: "待核验", ineligible: "不符合" }[m.state]) : "未计算";
      var dot = { 可比较: "ok", 待核验: "wait", 不符合: "no", 未计算: "info" }[label];
      var shown = state.supplyFilter === "all" || label === state.supplyFilter;
      return "<tr data-state-label='" + esc(label) + "'" + (shown ? "" : " hidden") + ">"
        + "<td>" + esc(s.supply_id) + "</td><td>" + esc(s.supplier_name) + "</td>"
        + "<td><span class='dot " + dot + "'></span>" + label + "</td>"
        + "<td>" + esc(s.material_name_raw) + "</td><td>" + fmt(s.price_cny_per_t) + "</td>"
        + "<td>" + fmt(s.distance_km) + "</td><td>" + fmt(s.available_mass_t, "t") + "</td>"
        + "<td>" + (s.evidence_source ? esc(s.evidence_source) : "<span class='tag wait'>缺证据</span>") + "</td>"
        + "<td><button data-del-supply='" + esc(s.supply_id) + "'>删除</button></td></tr>";
    }).join("");
    q("tab-supplies").innerHTML =
      "<div class='card'><h3>供给批次（" + state.supplies.length + " 条）</h3>"
      + "<p class='empty'>状态来自最近一次计算快照；「未计算」表示尚未运行引擎。缺值排序约定：缺失值排最前，显示为 -。</p>"
      + "<div>" + filterBtns + "</div>"
      + "<div class='search'><input id='supply-search' placeholder='搜索批次/供应商/物料（零结果时给建议）' aria-label='供给搜索'>"
      + "<button id='btn-supply-add'>单条录入</button></div>"
      + "<div id='supply-search-suggest' class='empty'></div>"
      + "<div class='table-scroll'><table><thead><tr>"
      + "<th>批次</th><th>供应商</th><th>状态</th><th>物料</th><th>价格 CNY/t</th><th>距离 km</th>"
      + "<th>可供 t</th><th>证据</th><th></th></tr></thead><tbody>" + rows + "</tbody></table></div>"
      + "<h3>CSV 导入（模板下载 → 预览 → 纠错 → 提交，幂等策略：同 supply_id 覆盖）</h3>"
      + "<div class='form-actions'><button id='btn-download-template'>下载 CSV 模板</button>"
      + "<button id='btn-import-preview'>预览校验</button>"
      + "<button class='primary' id='btn-import-commit'>校验通过后提交</button></div>"
      + "<textarea id='import-csv' rows='8' placeholder='粘贴 CSV 内容（含表头），或上传文件' style='width:100%'></textarea>"
      + "<div class='form-actions'><input type='file' id='import-file' accept='.csv,text/csv'></div>"
      + "<div id='import-result'>" + (state.importMessage || "") + "</div></div>";
    q("tab-supplies").querySelectorAll(".state-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        state.supplyFilter = b.dataset.state;
        renderSupplies();
      });
    });
    q("tab-supplies").querySelectorAll("[data-del-supply]").forEach(function (b) {
      b.addEventListener("click", function () {
        // 破坏性操作二次确认：删除后该批次退出候选池，已算方案会标记过期
        if (!confirm("确定删除供给批次 " + b.dataset.delSupply
          + "？删除后需重新录入才能参与计算。")) return;
        API.del("/tasks/" + encodeURIComponent(state.task.id) + "/supplies/" + encodeURIComponent(b.dataset.delSupply))
          .then(function () { state.stale = true; return API.get("/tasks/" + encodeURIComponent(state.task.id)); })
          .then(function (data) { state.supplies = data.supplies; renderSupplies(); })
          .catch(function (e) { alert("删除失败：" + e.message); });
      });
    });
    var searchInput = q("supply-search");
    searchInput.addEventListener("input", function () {
      var query = searchInput.value.trim().toLowerCase();
      var any = false;
      q("tab-supplies").querySelectorAll("tbody tr").forEach(function (tr) {
        var text = tr.textContent.toLowerCase();
        var show = !query || text.indexOf(query) >= 0;
        if (show && state.supplyFilter === "all") any = true;
        tr.hidden = !show;
      });
      if (query && !any) {
        q("supply-search-suggest").textContent = "零结果：可尝试「批次编号 / 供应商 / 物料词」；"
          + "若确无此供给，点击「单条录入」或导入 CSV 新增。";
      } else { q("supply-search-suggest").textContent = ""; }
    });
    q("btn-download-template").addEventListener("click", function () {
      var blob = new Blob([supplyTemplateCsv()], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "material_supply_template.csv";
      a.click();
    });
    q("import-file").addEventListener("change", function (e) {
      var file = e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () { q("import-csv").value = String(reader.result || ""); };
      reader.readAsText(file);
    });
    q("btn-import-preview").addEventListener("click", importPreview);
    q("btn-import-commit").addEventListener("click", function () { importCommit(true); });
    q("btn-supply-add").addEventListener("click", function () {
      var supply_id = prompt("新批次 supply_id（稳定唯一编号）");
      if (!supply_id) return;
      API.post("/tasks/" + encodeURIComponent(state.task.id) + "/supplies",
        { supply: { supply_id: supply_id } })
        .then(function () { return API.get("/tasks/" + encodeURIComponent(state.task.id)); })
        .then(function (data) { state.supplies = data.supplies; renderSupplies(); })
        .catch(function (e) { alert("录入失败：" + e.message); });
    });
  }
  async function importPreview() {
    var csvText = q("import-csv").value;
    try {
      var res = await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/supplies/import",
        { csv_text: csvText, commit: false });
      var p = res.preview;
      var html = "<div class='note'>预览 " + p.preview_count + " 行：结构错误 " + p.structural.length
        + " 项，业务待补证 " + p.business.length + " 项。"
        + (res.can_commit ? "可以提交。" : "存在结构错误，修正后才能提交（系统不静默丢行）。") + "</div>";
      if (p.issues.length) {
        html += "<div class='table-scroll'><table><thead><tr><th>行</th><th>列</th><th>级别</th>"
          + "<th>问题</th><th>修复建议</th></tr></thead><tbody>"
          + p.issues.map(function (i) {
            return "<tr><td>" + i.row + "</td><td>" + esc(i.col) + "</td><td>"
              + (i.severity === "structure" ? "<span class='tag no'>结构错误</span>" : "<span class='tag wait'>业务待补证</span>")
              + "</td><td>" + esc(i.problem) + "</td><td>" + esc(i.suggestion) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      } else {
        html += "<div class='okline'>未发现行级问题。</div>";
      }
      q("import-result").innerHTML = html;
    } catch (e) { q("import-result").innerHTML = "<div class='error'>" + esc(e.message) + "</div>"; }
  }
  async function importCommit(confirmNeeded) {
    var csvText = q("import-csv").value;
    try {
      var res = await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/supplies/import",
        { csv_text: csvText, commit: true });
      // 重渲染整个标签页会清掉结果区：先存消息，渲染后原样带回
      state.stale = true;  // 导入改变供给后旧结果过期
      state.importMessage = "<div class='okline'>已写入 " + res.written
        + " 条供给（幂等：同 supply_id 覆盖）。业务待补证 " + (res.business_issues || []).length
        + " 项：缺失字段保留为未知，走证据任务流程补证。</div>";
      q("import-result").innerHTML = state.importMessage;
      var data = await API.get("/tasks/" + encodeURIComponent(state.task.id));
      state.supplies = data.supplies;
      renderSupplies();
    } catch (e) {
      state.importMessage = "<div class='error'>提交被拒绝：" + esc(e.message) + "</div>";
      q("import-result").innerHTML = state.importMessage;
    }
  }

  /* —— 3 候选与证据任务 ——
     取数与渲染分离（loadEvidence 一次进入只发一次请求）；证据任务为
     完整可操作流程：创建 → 提交（结构化证据）→ 审核通过/驳回 → 重新提交；
     passed 生成不可静默覆盖的证据版本并写回供应快照，随后引导重算。 */
  var evidenceState = { filter: "all", search: "", page: 1, pageSize: 10 };

  function renderEvidence() {
    if (!state.task) { q("tab-evidence").innerHTML = "<p class='empty'>先保存需求并录入供给。</p>"; return; }
    if (!state.run) {
      q("tab-evidence").innerHTML = "<div class='card'><p class='empty'>尚未计算："
        + "请到「4 方案比较」点击「运行计算」，计算完成后回到本页查看候选与证据价值排序。</p>"
        + "<button class='primary' data-goto-compare='1'>去计算</button></div>";
      q("tab-evidence").querySelector("[data-goto-compare]").addEventListener("click", function () {
        setTab("compare");
      });
      return;
    }
    var pending = state.run.result.match_states.filter(function (s) { return s.state === "pending_evidence"; });
    var pendingById = {};
    pending.forEach(function (s) { pendingById[s.supply_id] = s; });
    // 证据任务区四态
    var etHtml;
    if (state.evidenceLoading) {
      etHtml = "<p class='empty' role='status'>证据任务加载中…</p>";
    } else if (state.evidenceError) {
      etHtml = "<div class='error'>证据任务加载失败：" + esc(state.evidenceError) + "</div>"
        + "<button class='evidence-retry'>重试</button>";
    } else if (!state.evidenceTasks || !state.evidenceTasks.length) {
      etHtml = "<p class='empty'>暂无证据任务。为待核验候选创建补证任务：</p>";
    } else {
      var list = state.evidenceTasks.filter(function (t) {
        if (evidenceState.filter !== "all" && t.status !== evidenceState.filter) return false;
        var qText = evidenceState.search.toLowerCase();
        if (qText && !(String(t.supply_id + " " + t.assignee + " " + t.action_id).toLowerCase().indexOf(qText) >= 0)) return false;
        return true;
      });
      var totalPages = Math.max(1, Math.ceil(list.length / evidenceState.pageSize));
      var page = Math.min(evidenceState.page, totalPages);
      var pageItems = list.slice((page - 1) * evidenceState.pageSize, page * evidenceState.pageSize);
      var statusLabels = { open: ["进行中", "wait"], submitted: ["已提交", "info"],
        passed: ["已通过", "ok"], rejected: ["已驳回", "no"], resubmit: ["重新提交", "wait"],
        closed: ["关闭", "info"] };
      var rows = pageItems.map(function (t) {
        var label = statusLabels[t.status] || [t.status, "info"];
        var actions = "";
        if (t.status === "open" || t.status === "resubmit" || t.status === "rejected") {
          actions = "<button class='et-submit' data-et='" + t.id + "'>填写并提交证据</button>";
        }
        if (t.status === "submitted") {
          actions = "<button class='et-pass' data-et='" + t.id + "'>审核通过</button>"
            + "<button class='et-reject' data-et='" + t.id + "'>驳回</button>";
        }
        return "<tr><td>" + esc(t.supply_id) + "</td><td>" + esc(t.action_id) + "</td>"
          + "<td>" + esc(t.assignee || "-") + "</td><td>" + esc(t.due_date || "-") + "</td>"
          + "<td><span class='tag " + label[1] + "'>" + label[0] + "</span></td>"
          + "<td>" + esc(t.evidence_ref || "-") + "</td>"
          + "<td>" + esc(t.verdict_reason || "-") + "</td>"
          + "<td>" + actions + "</td></tr>";
      }).join("");
      etHtml = "<div class='search'>"
        + "<input id='et-search' placeholder='搜索批次/责任人/动作（零结果给建议）' value='" + esc(evidenceState.search) + "'>"
        + "<select id='et-filter'>"
        + ["all", "open", "submitted", "passed", "rejected", "resubmit", "closed"].map(function (f) {
          return "<option value='" + f + "'" + (evidenceState.filter === f ? " selected" : "") + ">"
            + ({ all: "全部状态", open: "进行中", submitted: "已提交", passed: "已通过",
                 rejected: "已驳回", resubmit: "重新提交", closed: "关闭" }[f]) + "</option>";
        }).join("") + "</select>"
        + "<span class='empty'>第 " + page + "/" + totalPages + " 页</span>"
        + "<button id='et-prev'" + (page <= 1 ? " disabled" : "") + ">上一页</button>"
        + "<button id='et-next'" + (page >= totalPages ? " disabled" : "") + ">下一页</button></div>"
        + (list.length ? "<div class='table-scroll'><table><thead><tr><th>批次</th><th>动作</th>"
          + "<th>责任人</th><th>截止</th><th>状态</th><th>证据引用</th><th>理由</th><th>操作</th></tr></thead><tbody>"
          + rows + "</tbody></table></div>"
          : "<p class='empty'>零结果：换关键词/状态，或为下方待核验候选创建新任务。</p>");
    }
    // 证据版本历史（可回放）
    var versionsHtml = state.evidenceVersions && state.evidenceVersions.length
      ? "<div class='table-scroll'><table><thead><tr><th>批次</th><th>版本</th><th>类型</th>"
        + "<th>机构</th><th>检测日期</th><th>有效期至</th><th>状态</th><th>实测字段</th></tr></thead><tbody>"
        + state.evidenceVersions.map(function (v) {
          var fields = Object.keys(v.measured_fields_json ? JSON.parse(v.measured_fields_json) : {});
          return "<tr><td>" + esc(v.supply_id) + "</td><td>v" + v.version_no + "</td>"
            + "<td>" + esc(v.evidence_type || "-") + "</td><td>" + esc(v.issuing_body || "-") + "</td>"
            + "<td>" + esc(v.measured_date || "-") + "</td><td>" + esc(v.valid_until || "-") + "</td>"
            + "<td><span class='tag " + (v.status === "passed" ? "ok" : "info") + "'>"
            + ({ passed: "生效", superseded: "已替代" }[v.status] || v.status) + "</span></td>"
            + "<td>" + esc(fields.join("、") || "-") + "</td></tr>";
        }).join("") + "</tbody></table></div>"
      : "<p class='empty'>暂无证据版本。</p>";
    q("tab-evidence").innerHTML =
      "<div class='card'><h3>待核验候选（" + pending.length + " 个）· 缺什么—谁补—补了什么—谁审—何时生效</h3>"
      + (pending.length ? "<p class='empty'>" + pending.map(function (s) {
        return esc(s.supplier_name) + "：" + esc((s.pending_reasons || []).join("；"));
      }).join("<br>") + "</p>" : "<p class='okline'>当前证据下无待核验批次。</p>")
      + "<h3>创建补证任务</h3>"
      + "<div class='field-row'>"
      + "<div><label><b>批次</b></label><select id='et-new-supply'>"
      + pending.map(function (s) { return "<option value='" + esc(s.supply_id) + "'>" + esc(s.supply_id) + "</option>"; }).join("")
      + "</select></div>"
      + "<div><label><b>动作</b></label><select id='et-new-action'>"
      + "<option>DETECTION_REPORT</option><option>PRICE_QUOTE</option>"
      + "<option>ROUTE_CHECK</option><option>CAPACITY_COMMITMENT</option></select></div>"
      + "<div><label><b>责任人</b></label><input id='et-new-assignee'></div>"
      + "<div><label><b>截止日</b></label><input id='et-new-due' placeholder='YYYY-MM-DD'></div>"
      + "<div style='align-self:end'><button class='primary' id='et-create'>创建任务</button></div></div>"
      + "<span class='empty' id='et-msg'></span>"
      + "<h3>证据任务（" + (state.evidenceTasks || []).length + " 条）</h3>" + etHtml
      + "<h3>证据价值排序（先补哪个最值）</h3>"
      + "<div class='table-scroll'><table><thead><tr><th>动作</th><th>批次</th><th>阻断字段</th>"
      + "<th>可释放供给 t</th><th>成本节约上限 CNY</th><th>净减排增量 kgCO2e</th><th>服务率提升</th>"
      + "<th>费用</th><th>概率口径</th></tr></thead><tbody>"
      + (state.run.result.evidence_plans || []).map(function (p) {
        return "<tr><td>" + esc(p.action_name) + "</td><td>" + esc(p.supply_id) + "</td>"
          + "<td>" + esc((p.blocked_fields || []).join("、")) + "</td>"
          + "<td>" + fmt(p.value_interval.extra_supply_t, "t") + "</td>"
          + "<td>" + fmt(p.value_interval.cost_saving_max_cny, "int") + "</td>"
          + "<td>" + fmt(p.value_interval.net_avoided_gain_kgco2e, "int") + "</td>"
          + "<td>" + fmt(p.value_interval.resilience_improvement, "pct") + "</td>"
          + "<td>" + esc(p.fee_cny) + "</td><td>" + esc(p.probability) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
      + "<h3>证据版本历史（为什么当时通过/不通过可回放）</h3>" + versionsHtml
      + "<h3>证据质量多维评分（1 最优；启发式合成，非统计置信度）</h3>"
      + "<div class='table-scroll'><table><thead><tr><th>批次</th><th>供应商</th><th>质量分</th>"
      + "<th>等级</th><th>最短板</th><th>日期标记</th><th>关键缺证据</th><th>门槛状态</th></tr></thead><tbody>"
      + (state.run.result.quality || []).map(function (r) {
        return "<tr><td>" + esc(r.supply_id) + "</td><td>" + esc(r.supplier_name) + "</td>"
          + "<td>" + r.score + "</td><td>" + esc(r.grade) + "</td><td>" + esc(r.weakest || "无短板") + "</td>"
          + "<td>" + esc((r.flags || []).join("、") || "-") + "</td>"
          + "<td>" + esc((r.critical_missing || []).join("、") || "-") + "</td>"
          + "<td>" + esc(r.gate_label) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
      + "<div id='et-form-anchor'></div>"
      + "</div>";
    bindEvidence();
  }

  function bindEvidence() {
    // 每次渲染后绑定新节点（节点随 innerHTML 重建，不堆积监听）
    var retry = q("tab-evidence").querySelector(".evidence-retry");
    if (retry) { retry.addEventListener("click", function () { state.evidenceLoaded = false; loadEvidence(); }); }
    var search = q("et-search");
    if (search) {
      search.addEventListener("input", function () {
        evidenceState.search = search.value.trim();
        evidenceState.page = 1;
        renderEvidence();
      });
    }
    var filter = q("et-filter");
    if (filter) {
      filter.addEventListener("change", function () {
        evidenceState.filter = filter.value;
        evidenceState.page = 1;
        renderEvidence();
      });
    }
    var prev = q("et-prev"), next = q("et-next");
    if (prev) { prev.addEventListener("click", function () { evidenceState.page -= 1; renderEvidence(); }); }
    if (next) { next.addEventListener("click", function () { evidenceState.page += 1; renderEvidence(); }); }
    var createBtn = q("et-create");
    if (createBtn) {
      createBtn.addEventListener("click", function () {
        var supplySel = q("et-new-supply");
        if (!supplySel) { setMsg("当前没有待核验候选可建任务（所有批次证据齐全或已淘汰）。"); return; }
        API.post("/tasks/" + encodeURIComponent(state.task.id) + "/evidence-tasks", {
          supply_id: supplySel.value,
          action_id: q("et-new-action").value,
          assignee: q("et-new-assignee").value.trim(),
          due_date: q("et-new-due").value.trim(),
        }).then(function () {
          setMsg("任务已创建。");
          loadEvidence();
        }).catch(function (e) { setMsg("创建失败：" + e.message); });
      });
    }
    function setMsg(msg) { var el = q("et-msg"); if (el) el.textContent = msg; }
    q("tab-evidence").querySelectorAll(".et-submit").forEach(function (b) {
      b.addEventListener("click", function () { openEvidenceForm(b.dataset.et, "submit"); });
    });
    q("tab-evidence").querySelectorAll(".et-pass").forEach(function (b) {
      b.addEventListener("click", function () { openEvidenceForm(b.dataset.et, "pass"); });
    });
    q("tab-evidence").querySelectorAll(".et-reject").forEach(function (b) {
      b.addEventListener("click", function () {
        var reason = prompt("驳回原因（必填）");
        if (!reason) return;
        API.put("/tasks/" + encodeURIComponent(state.task.id) + "/evidence-tasks/" + b.dataset.et,
          { status: "rejected", verdict_reason: reason })
          .then(function (res) { loadEvidence().then(function () { setMsg(res.note || "已驳回，门槛保持不变。"); }); })
          .catch(function (e) { setMsg("操作失败：" + e.message); });
      });
    });
  }

  function openEvidenceForm(etId, mode) {
    var task = (state.evidenceTasks || []).find(function (t) { return String(t.id) === String(etId); });
    if (!task) { return; }
    var isPass = mode === "pass";
    q("et-form-anchor").innerHTML = "<div class='card' id='et-form'>"
      + "<h3>" + (isPass ? "审核证据（批次 " : "提交证据（批次 ") + esc(task.supply_id) + "）</h3>"
      + (isPass
        ? "<p class='empty'>审核人：" + esc(state.me ? state.me.username : "") + "。通过会生成证据版本并写回批次快照；驳回保持门槛。</p>"
        : "<p class='empty'>结构化证据必填：类型/来源/附件/机构/检测日期/有效期/关键实测字段。</p>")
      + "<div class='field-row'>"
      + "<div><label><b>证据类型*</b></label><input id='ev-type' value='" + esc(task.evidence_ref || "") + "'></div>"
      + "<div><label><b>来源/报告编号*</b></label><input id='ev-source' placeholder='报告编号 R-XXXX'></div>"
      + "<div><label><b>附件引用</b></label><input id='ev-attach'></div>"
      + "<div><label><b>出具机构*</b></label><input id='ev-body'></div>"
      + "<div><label><b>检测/核验日期*</b></label><input id='ev-mdate' placeholder='YYYY-MM-DD'></div>"
      + "<div><label><b>有效期至</b></label><input id='ev-valid' placeholder='YYYY-MM-DD'></div>"
      + "</div>"
      + "<div class='field-row'>"
      + "<div><label><b>实测熔指 g/10min</b></label><input id='ev-mfi' type='number' step='any'></div>"
      + "<div><label><b>实测水分 %</b></label><input id='ev-moist' type='number' step='any'></div>"
      + "<div><label><b>实测灰分 %</b></label><input id='ev-ash' type='number' step='any'></div>"
      + "<div><label><b>实测再生含量 %</b></label><input id='ev-rc' type='number' step='any'></div>"
      + "</div>"
      + "<label><b>审核/提交说明</b></label><input id='ev-reason' style='width:100%'>"
      + "<div class='form-actions'>"
      + "<button class='primary' id='ev-confirm'>" + (isPass ? "审核通过（生成证据版本）" : "提交证据") + "</button>"
      + "<button id='ev-cancel'>取消</button></div>"
      + "<span class='empty' id='ev-form-msg'></span></div>";
    q("ev-cancel").addEventListener("click", function () { q("et-form-anchor").innerHTML = ""; });
    q("ev-confirm").addEventListener("click", function () {
      var fields = {};
      [["ev-mfi", "mfi_g_10min"], ["ev-moist", "moisture_pct"],
       ["ev-ash", "ash_pct"], ["ev-rc", "recycled_content_pct"]].forEach(function (pair) {
        var v = q(pair[0]).value;
        if (v !== "") { fields[pair[1]] = Number(v); }
      });
      var evidence = { evidence_type: q("ev-type").value.trim(),
        source_ref: q("ev-source").value.trim(),
        attachment_ref: q("ev-attach").value.trim(),
        issuing_body: q("ev-body").value.trim(),
        measured_date: q("ev-mdate").value.trim(),
        valid_until: q("ev-valid").value.trim(),
        measured_fields: fields };
      var payload = { status: isPass ? "passed" : "submitted", evidence: evidence,
        verdict_reason: q("ev-reason").value.trim() };
      API.put("/tasks/" + encodeURIComponent(state.task.id) + "/evidence-tasks/" + etId, payload)
        .then(function (res) {
          state.evidenceLoaded = false;
          state.stale = true;  // 证据变化 → 旧结果过期（前端提示）
          return loadEvidence().then(function () {
            q("et-msg").textContent = res.note || (isPass ? "已通过并生成证据版本。" : "已提交，等待审核。");
          });
        })
        .catch(function (e) {
          var box = q("ev-form-msg");
          if (box) {
            var details = e.details || [];
            box.textContent = "操作失败：" + e.message
              + (details.length ? "（" + details.map(function (d) { return d.field + "：" + d.problem; }).join("；") + "）" : "");
          }
        });
    });
  }

  function loadEvidence() {
    // 一次进入只发一次请求；响应回调只更新状态+单次渲染，不递归取数
    if (!state.task || state.evidenceLoading) return Promise.resolve();
    state.evidenceLoading = true;
    state.evidenceError = null;
    renderEvidence();
    return Promise.all([
      API.get("/tasks/" + encodeURIComponent(state.task.id) + "/evidence-tasks"),
      API.get("/tasks/" + encodeURIComponent(state.task.id) + "/evidence-versions"),
    ]).then(function (results) {
      state.evidenceTasks = results[0].evidence_tasks || [];
      state.evidenceVersions = results[1].evidence_versions || [];
      state.evidenceLoaded = true;
      state.evidenceError = null;
    }).catch(function (e) {
      state.evidenceLoaded = false;
      state.evidenceError = e.message || "加载失败";
    }).then(function () {
      state.evidenceLoading = false;
      if (state.tab === "evidence") renderEvidence();
    });
  }

  /* —— 4 方案比较 —— */
  function renderCompare() {
    if (!state.task) { q("tab-compare").innerHTML = "<p class='empty'>先保存需求并录入供给。</p>"; return; }
    var runSection = state.run
      ? "<div class='okline'>最近快照 <b>" + esc(state.run.id) + "</b>（" + esc(state.run.created_at) + "）"
        + " · 计算耗时 " + (state.run.meta && state.run.meta.elapsed_ms) + " ms"
        + " · 因子配置哈希 " + esc(state.run.config_sha256.slice(0, 12)) + "</div>"
      : "<div class='note'>尚未计算。点击「运行计算」用共用核心生成快照（含完整方案集/Pareto/N-1/敏感性/护照）。</div>";
    q("tab-compare").innerHTML =
      "<div class='card'><h3>计算与假设重算</h3>" + runSection
      + (state.runs && state.runs.length > 1
        ? "<div><label><b>历史运行（查看任一版本；旧版本确认受服务端新鲜度校验）</b></label><select id='run-select'>"
          + state.runs.map(function (r) {
            return "<option value='" + esc(r.id) + "'" + (state.run && r.id === state.run.id ? " selected" : "") + ">"
              + esc(r.id.slice(0, 12)) + "（" + esc(r.created_at) + "）</option>";
          }).join("") + "</select></div>" : "")
      + "<div class='form-actions'>"
      + "<button class='primary' id='btn-run'>运行计算</button>"
      + "<button id='btn-toggle-optimistic'" + (state.run ? "" : " disabled") + ">保守/乐观口径</button>"
      + "<span id='mode-label'>" + (state.optimistic ? "乐观（假定待核验证据通过）" : "保守（现状证据）") + "</span>"
      + "</div>"
      + "<h3>假设调整后重算（数量/距离/运价/因子假设，共用核心）</h3>"
      + "<p class='empty'>直接编辑需求（第 1 步）或供给数据后重新运行计算；重算前旧结果会被标记为过期（stale），"
      + "新结果与旧值并排展示差值，失效约束如实列出。静态版只允许已有预计算场景，不滑杆预写数字。</p>"
      + "</div><div id='compare-results'></div>";
    q("btn-run").addEventListener("click", runEngine);
    q("btn-toggle-optimistic").addEventListener("click", function () {
      state.optimistic = !state.optimistic;
      renderCompare();
    });
    var runSel = q("run-select");
    if (runSel) {
      runSel.addEventListener("change", function () {
        loadRun(runSel.value).then(function () { renderCompare(); });
      });
    }
    if (state.run) renderCompareResults();
  }
  var ASYNC_RUN_THRESHOLD = 16;  // 供给批次 ≥ 该数自动走后台计算（v18 任务 3）
  async function runEngine() {
    var btn = q("btn-run");
    btn.disabled = true;  // 防重复提交：计算期间不可再点
    var useAsync = (state.supplies || []).length >= ASYNC_RUN_THRESHOLD;
    try {
      var res;
      if (useAsync) {
        res = await API.post("/tasks/" + encodeURIComponent(state.task.id)
          + "/runs?async=1", {});
        var job = await pollJob(btn, res.job_id);
        res = { run_id: job.run_id, elapsed_ms: job.elapsed_ms };
      } else {
        btn.textContent = "计算中…（超时/失败会如实显示）";
        res = await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/runs");
      }
      var data = await API.get("/runs/" + res.run_id);
      state.run = { id: data.run.id, created_at: data.run.created_at,
        config_sha256: data.run.config_sha256, result: data.result,
        meta: { elapsed_ms: res.elapsed_ms } };
      state.optimistic = false;
      state.stale = false;  // 重算后结果新鲜
      renderCompare();
    } catch (e) {
      alert("计算失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "运行计算";
    }
  }
  async function pollJob(btn, jobId) {
    // 轮询后台作业（大案例预计十几分钟；2 秒间隔，实时显示已用时）
    var t0 = Date.now();
    for (;;) {
      var job = await API.get("/runs/jobs/" + jobId);
      if (job.status === "done") return job;
      if (job.status === "failed") throw new Error(job.error || "后台计算失败");
      var secs = Math.round((Date.now() - t0) / 1000);
      btn.textContent = "后台计算中…已用时 " + secs
        + " 秒（大案例预计十几分钟，请保持页面打开）";
      await new Promise(function (r) { setTimeout(r, 2000); });
    }
  }
  function renderCompareResults() {
    var r = state.run.result;
    var scenarioKey = state.optimistic ? "optimistic" : "conservative";
    var scen = (r.scenarios || {})[scenarioKey] || null;
    var options = scen ? scen.options : (r.options || []);
    var baseline = options.find(function (o) { return o.kind === "baseline"; });
    var baseCost = baseline && baseline.total_cost_cny != null ? baseline.total_cost_cny : null;
    // 稳定 ID 列表：去重
    var seen = {};
    var unique = options.filter(function (o) { if (seen[o.option_id]) return false; seen[o.option_id] = true; return true; });
    var dropped = state.selectedIds.filter(function (id) { return !seen[id]; });
    var selectedSet = state.selectedIds.filter(function (id) { return seen[id]; });
    if (dropped.length) {
      state.selectedIds = selectedSet;
      console.warn("已清理失效选择：" + dropped.join(", "));
    }
    var dropNote = dropped.length
      ? "<div class='note'>" + dropped.length + " 个已选方案在当前口径下不存在，已从对比中移除"
        + "（切换场景后失效选择提示并清理，不悄悄顶替）。</div>" : "";
    var staleNote = state.stale ? "<div class='note'>⚠ 数据已修改，当前结果已过期（stale）——请重新运行计算。</div>" : "";
    var rows = unique.map(function (o) {
      var selected = selectedSet.indexOf(o.option_id) >= 0;
      var savingRatio = (baseCost != null && o.total_cost_cny != null && o.kind !== "baseline")
        ? Math.max(0, Math.min(1, (baseCost - o.total_cost_cny) / baseCost)) : 0;
      var delta = "";
      if (baseCost != null && o.total_cost_cny != null && o.kind !== "baseline") {
        var d = o.total_cost_cny - baseCost;
        delta = (d < 0 ? "省 " : "多花 ") + Math.abs(d).toLocaleString("zh-CN") + " CNY";
      }
      return "<tr data-option-id='" + esc(o.option_id) + "' class='" + (selected ? "row-hl" : "") + "'>"
        + "<td><input type='checkbox' class='pick-option' data-option-id='" + esc(o.option_id) + "'"
        + (selected ? " checked" : "") + (selectedSet.length >= 3 && !selected ? " disabled" : "")
        + " aria-label='选择方案'></td>"
        + "<td>" + esc(o.label) + "</td><td>" + esc({ baseline: "基准", single: "单供", combo: "组合" }[o.kind] || o.kind) + "</td>"
        + "<td>" + fmt(o.total_cost_cny, "cny") + "</td><td>" + fmt(o.net_avoided_kgco2e, "int") + "</td>"
        + "<td>" + fmt(o.order_max_share, "pct") + "</td><td>" + esc(o.category) + "</td>"
        + "<td>" + esc(delta) + "</td>"
        + "<td><button class='details-toggle' data-option-id='" + esc(o.option_id) + "'>明细</button></td></tr>"
        + "<tr class='details-body' data-details='" + esc(o.option_id) + "' hidden><td colspan='9'>"
        + optionDetails(o) + "</td></tr>";
    }).join("");
    var pareto = scen ? scen.pareto : [];
    var paretoSvg = renderPareto(pareto);
    var n1 = scen ? scen.n1 : (r.n1_stress || []);
    var n1Rows = n1.map(function (e) {
      return "<tr data-option-id='" + esc(e.option_id) + "'><td>" + esc(e.label) + "</td><td>"
        + fmt(e.min_service, "pct") + "</td><td>" + esc(e.worst) + "</td><td>" + esc(e.spof || "无") + "</td></tr>";
    }).join("");
    var sens = r.sensitivity;
    var selNote = selectedSet.length < 2
      ? "<p class='empty'>方案比较：勾选 2–3 个方案（稳定 ID）对比。不足两项时无法比较："
        + "原因可能是完整方案集过小或全被硬门槛/缺价淘汰（如实显示，不悄悄用另一方案顶替）。</p>" : "";
    var confirmSelHtml = unique.filter(function (o) { return o.kind !== "baseline" && o.allocation && Object.keys(o.allocation).length; })
      .map(function (o) {
        return "<option value='" + esc(o.option_id) + "'>" + esc(o.label) + "</option>";
      }).join("");
    q("compare-results").innerHTML = staleNote + dropNote +
      "<div class='card'><h3>完整方案集（" + unique.length + " 个，按 option_id 对齐；分页不影响计算）</h3>"
      + "<div class='table-scroll'><table><thead><tr><th>选</th><th>方案</th><th>类型</th><th>到厂总成本 CNY</th>"
      + "<th>净减排 kgCO2e</th><th>订单最大份额</th><th>分类</th><th>较基准</th><th>明细</th></tr></thead><tbody>"
      + rows + "</tbody></table></div>"
      + selNote
      + "<div id='compare-cards'></div>"
      + "<h3>Pareto 前沿（二维投影不代表三维完整前沿；颜色+标签双重区分，不只用颜色）</h3>"
      + "<div class='pareto-wrap' id='pareto-wrap-workbench'>" + paretoSvg + "</div>"
      + "<h3>N-1 断供压力测试（失效实体=供应商；两张对照分开归因）</h3>"
      + "<div class='table-scroll'><table><thead><tr><th>方案</th><th>最低交付服务率</th>"
      + "<th>最坏情况</th><th>单点故障</th></tr></thead><tbody>" + n1Rows + "</tbody></table></div>"
      + "<p class='empty'>" + esc((r.n1_compare_strategies || {}).conclusion_note || "")
      + (r.n1_compare_evidence && r.n1_compare_evidence.delta_service_rate != null
        ? " 补证对照（固定方案）：服务率变化 " + fmt(r.n1_compare_evidence.delta_service_rate, "pct") + "。"
        : "") + "</p>"
      + (sens ? "<h3>结论稳定性（" + esc(sens.verdict) + "）</h3><p class='empty'>"
        + sens.sign_flips + " 处符号翻转；" + sens.selection_changes + " 个场景代表集合变化。"
        + " 翻转阈值与联合情景见详细快照。</p>" : "")
      + "<h3>比较选定（可 2–3 个，只做对比，不占用库存）</h3>"
      + "<label><b>对比理由</b></label><textarea id='select-reason' rows='2' style='width:100%'"
      + " placeholder='例如：优先韧性——本地 8t+邻区 7t 组合断供服务率最高，绿色溢价可接受'></textarea>"
      + "<div class='form-actions'><button class='primary' id='btn-select'>保存对比选定</button>"
      + "<span class='empty' id='select-msg'></span></div>"
      + "<h3>确认采购（与比较分离：只确认一个方案或一个明确组合，占用实时库存）</h3>"
      + "<div><label><b>确认方案（单选）</b></label><select id='confirm-option'></select></div>"
      + "<label><b>确认理由</b></label><input id='confirm-reason' style='width:100%'"
      + " placeholder='最终确认理由（影响/风险/证据缺口/库存占用）'>"
      + "<div class='form-actions'><button class='primary' id='btn-confirm'>确认采购</button>"
      + "<span class='empty' id='confirm-msg'></span></div>"
      + "<p class='empty'>确认会校验快照新鲜度（价格/库存/交期/证据/规则变化将阻止并引导重算），"
      + "成功后为方案各批次生成 confirmed 预留（全局库存占用）。</p></div>";
    var selBox = q("confirm-option");
    if (selBox) {
      var prev = selBox.value;
      selBox.innerHTML = confirmSelHtml;
      if (prev) selBox.value = prev;
    }
    var confirmBtn = q("btn-confirm");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", confirmPurchase);
    }
    bindCompareInteractions(scenarioKey, unique, scen);
    markSelectionsInCompare();
  }
  function markSelectionsInCompare() {
    // 用户视角可追溯：在当前运行快照的方案行上标注「已选定对比/已确认采购」，
    // 刷新页面后仍可见（读取服务端 selections，confirm 产生的记录带「已确认」标记）。
    if (!state.task || !state.run) { return; }
    var runId = state.run.id;
    API.get("/tasks/" + encodeURIComponent(state.task.id) + "/selections").then(function (data) {
      var byOption = {};
      (data.selections || []).forEach(function (s) {
        if (s.run_id !== runId) { return; }
        if (!byOption[s.option_id]) { byOption[s.option_id] = []; }
        byOption[s.option_id].push(s);
      });
      Object.keys(byOption).forEach(function (oid) {
        var row = q("compare-results").querySelector("tr[data-option-id='" + CSS.escape(oid) + "']");
        if (!row) { return; }
        var confirmed = byOption[oid].some(function (s) {
          return (s.reason || "").indexOf("已确认") >= 0;
        });
        var cell = row.querySelector("td:nth-child(2)");
        if (cell && cell.querySelector(".pick-state-tag") === null) {
          cell.innerHTML += " <span class='tag " + (confirmed ? "ok" : "info")
            + " pick-state-tag'>" + (confirmed ? "已确认采购" : "已选定对比") + "</span>";
        }
      });
    }).catch(function () { /* 标记失败不影响主流程 */ });
  }
  function optionDetails(o) {
    var alloc = (o.allocation || {});
    var allocText = Object.keys(alloc).map(function (k) {
      return esc((o.allocation_labels || {})[k] || k) + " " + alloc[k] + " t";
    }).join(" + ") || "—";
    var ledger = (o.cost_ledger && o.cost_ledger.lines || []).map(function (l) {
      return "<tr><td>" + esc(l.item) + "</td><td>" + fmt(l.activity, "t") + "</td><td>" + esc(l.unit) + "</td>"
        + "<td>" + esc(l.formula) + "</td><td>" + esc(l.source) + "</td><td>" + fmt(l.amount, "cny") + "</td></tr>";
    }).join("");
    var problems = (o.problems || []).map(esc).join("<br>") || "无";
    return "<div class='details-body'><b>分配</b>：" + allocText
      + (o.recycled_mass_t != null ? "；<b>实际再生质量</b>：" + fmt(o.recycled_mass_t, "t") + " t（= 交付量×再生含量，不足 100% 不全部计为循环材料）" : "")
      + "<br><b>到厂成本账本</b>："
      + "<table><thead><tr><th>分项</th><th>活动量</th><th>单位</th><th>公式</th><th>来源</th><th>金额 CNY</th></tr></thead><tbody>"
      + ledger + "</tbody></table>"
      + "<b>阻断/条件</b>：" + problems + "</div>";
  }
  function bindCompareInteractions(scenarioKey, unique, scen) {
    q("compare-results").querySelectorAll(".details-toggle").forEach(function (b) {
      b.addEventListener("click", function () {
        var row = q("compare-results").querySelector("[data-details='" + CSS.escape(b.dataset.optionId) + "']");
        if (row) row.hidden = !row.hidden;
      });
    });
    q("compare-results").querySelectorAll(".pick-option").forEach(function (cb) {
      cb.addEventListener("change", function () {
        if (cb.checked) { if (state.selectedIds.length >= 3) { cb.checked = false; return; } state.selectedIds.push(cb.dataset.optionId); }
        else { state.selectedIds = state.selectedIds.filter(function (id) { return id !== cb.dataset.optionId; }); }
        renderCompareResults();
      });
    });
    q("compare-results").querySelectorAll("#btn-select").forEach(function (b) {
      b.addEventListener("click", selectOptions);
    });
    var wrap = q("compare-results").querySelector(".pareto-wrap");
    if (wrap) {
      wrap.addEventListener("click", function (ev) {
        var c = ev.target.closest ? ev.target.closest("circle[data-option-id]") : null;
        if (!c) return;
        var row = q("compare-results").querySelector("tr[data-option-id='" + CSS.escape(c.getAttribute("data-option-id")) + "']");
        if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
    renderCompareCards(unique, scen);
  }
  function highlightCompareRow(optionId, on) {
    q("compare-results").querySelectorAll("tr[data-option-id='" + CSS.escape(optionId) + "']")
      .forEach(function (tr) { tr.classList.toggle("row-hl", on); });
  }
  function renderCompareCards(unique, scen) {
    var selected = unique.filter(function (o) { return state.selectedIds.indexOf(o.option_id) >= 0; });
    if (selected.length < 2) { q("compare-cards").innerHTML = ""; return; }
    var n1Map = {};
    (scen ? scen.n1 : []).forEach(function (e) { n1Map[e.option_id] = e; });
    q("compare-cards").innerHTML = "<div class='grid'>" + selected.map(function (o) {
      var n1e = n1Map[o.option_id];
      var alloc = Object.keys(o.allocation || {}).map(function (k) {
        return esc((o.allocation_labels || {})[k] || k) + " " + o.allocation[k] + " t";
      }).join(" + ") || "—";
      return "<div class='kpi'><div class='cap'>" + esc(o.label) + "（" + esc(o.option_id.slice(0, 12)) + "）</div>"
        + "<div class='num'>" + fmt(o.total_cost_cny, "cny") + "</div>"
        + "<div class='cap'>到厂成本 CNY · 净减排 " + fmt(o.net_avoided_kgco2e, "int") + " kgCO2e"
        + " · 份额 " + fmt(o.order_max_share, "pct") + " · N-1 "
        + (n1e ? fmt(n1e.min_service, "pct") : "-") + "<br>分配：" + alloc + "</div></div>";
    }).join("") + "</div>";
  }
  async function confirmPurchase() {
    var box = q("confirm-msg");
    if (!state.run) { box.textContent = "先运行计算。"; return; }
    var optionId = q("confirm-option").value;
    if (!optionId) { box.textContent = "请选择要确认的方案。"; return; }
    try {
      var res = await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/confirm", {
        run_id: state.run.id, option_id: optionId,
        reason: q("confirm-reason").value.trim(),
      });
      box.textContent = "确认成功：" + res.note + "（占用批次 " + Object.keys(res.allocation || {}).join("、") + "）";
      markSelectionsInCompare();
    } catch (e) {
      box.textContent = "确认被拒绝：" + e.message;
      if (e.details && e.details.run_state_hash) {
        box.textContent += "（服务端新鲜度校验未通过——请点击「运行计算」后重试）";
      }
    }
  }

  async function selectOptions() {
    if (!state.run) { q("select-msg").textContent = "先运行计算。"; return; }
    if (state.selectedIds.length < 2) { q("select-msg").textContent = "请先勾选 2–3 个方案。"; return; }
    try {
      for (var i = 0; i < state.selectedIds.length; i++) {
        await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/selections",
          { run_id: state.run.id, option_id: state.selectedIds[i],
            reason: q("select-reason").value.trim() });
      }
      q("select-msg").textContent = "已保存选定方案与理由（绑定运行快照 " + state.run.id + "）。";
    } catch (e) { q("select-msg").textContent = "保存失败：" + e.message; }
  }
  function renderPareto(pts) {
    if (!pts || pts.length < 2) return "<p class='empty'>可绘制方案不足两个（缺价/缺证据方案不进图）。</p>";
    var costs = pts.map(function (p) { return p.cost; });
    var nets = pts.map(function (p) { return p.net; });
    var minc = Math.min.apply(null, costs), maxc = Math.max.apply(null, costs);
    var minn = Math.min.apply(null, nets), maxn = Math.max.apply(null, nets);
    var spanC = (maxc - minc) || 1, spanN = (maxn - minn) || 1;
    var pad = 0.06, W = 660, H = 340, L = 76, R = 18, T = 18, B = 46;
    function px(c) { return L + (c - minc) / spanC * (1 - 2 * pad) * (W - L - R) + pad * (W - L - R); }
    function py(n) { return T + (maxn - n) / spanN * (1 - 2 * pad) * (H - T - B) + pad * (H - T - B); }
    var parts = ["<line x1='" + L + "' y1='" + (H - B) + "' x2='" + (W - R) + "' y2='" + (H - B) + "' stroke='#9db3a6'/>",
      "<line x1='" + L + "' y1='" + T + "' x2='" + L + "' y2='" + (H - B) + "' stroke='#9db3a6'/>"];
    for (var i = 0; i < 5; i++) {
      var cx = minc + spanC * i / 4, ny = maxn - spanN * i / 4;
      parts.push("<line x1='" + px(cx).toFixed(1) + "' y1='" + (H - B) + "' x2='" + px(cx).toFixed(1) + "' y2='" + (H - B + 4) + "' stroke='#9db3a6'/>"
        + "<text x='" + px(cx).toFixed(1) + "' y='" + (H - B + 18) + "' font-size='11' fill='#5b6f65' text-anchor='middle'>"
        + cx.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) + "</text>");
      parts.push("<line x1='" + (L - 4) + "' y1='" + py(ny).toFixed(1) + "' x2='" + L + "' y2='" + py(ny).toFixed(1) + "' stroke='#9db3a6'/>"
        + "<text x='" + (L - 8) + "' y='" + (py(ny) + 4).toFixed(1) + "' font-size='11' fill='#5b6f65' text-anchor='end'>"
        + ny.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) + "</text>");
    }
    parts.push("<text x='14' y='16' font-size='11' fill='#5b6f65'>净减排 kgCO2e</text>");
    parts.push("<text x='" + (W - R) + "' y='" + (H - 8) + "' font-size='11' fill='#5b6f65' text-anchor='end'>到厂总成本 CNY</text>");
    var frontSorted = pts.filter(function (p) { return p.on_frontier; }).sort(function (a, b) { return a.cost - b.cost; });
    if (frontSorted.length > 1) {
      // 折线先于圆点绘制（不遮挡悬停），且 pointer-events:none（style.css）
      parts.push("<polyline points='" + frontSorted.map(function (p) {
        return px(p.cost).toFixed(1) + "," + py(p.net).toFixed(1);
      }).join(" ") + "' fill='none' stroke='var(--accent)' stroke-width='2.5' stroke-dasharray='5,4'/>");
    }
    pts.forEach(function (p) {
      var front = p.on_frontier;
      parts.push("<circle cx='" + px(p.cost).toFixed(1) + "' cy='" + py(p.net).toFixed(1) + "' r='"
        + (front ? 7 : 4.5) + "' fill='" + (front ? "var(--accent)" : "#b9ccc2")
        + "' stroke='" + (front ? "var(--accent-deep)" : "#8fa39a") + "' stroke-dasharray='" + (front ? "" : "3,2")
        + "' data-option-id='" + esc(p.option_id) + "' tabindex='0' role='button' aria-label='"
        + esc(p.label) + "：成本 " + p.cost.toLocaleString() + "，净减排 " + p.net.toLocaleString()
        + (front ? "，非支配前沿" : "，被支配") + "'>"
        + "<title>" + esc(p.label) + "：成本 " + p.cost.toLocaleString() + "，净减排 "
        + p.net.toLocaleString() + (front ? "，非支配前沿" : "，被支配") + "</title></circle>");
    });
    var svg = "<svg viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Pareto 前沿图'>" + parts.join("") + "</svg>";
    // 直接绑定圆点事件（mouseenter 不冒泡，容器级委托失效；节点随重渲染销毁，不堆积监听）
    setTimeout(function () {
      var wrapEl = q("pareto-wrap-workbench");
      if (!wrapEl) { return; }
      wrapEl.querySelectorAll("circle[data-option-id]").forEach(function (circle) {
        circle.addEventListener("mouseenter", function () {
          highlightCompareRow(circle.getAttribute("data-option-id"), true);
        });
        circle.addEventListener("mouseleave", function () {
          highlightCompareRow(circle.getAttribute("data-option-id"), false);
        });
        circle.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            highlightCompareRow(circle.getAttribute("data-option-id"), true);
            var row = q("compare-results").querySelector("tr[data-option-id='" + CSS.escape(circle.getAttribute("data-option-id")) + "']");
            if (row) { row.scrollIntoView({ behavior: "smooth", block: "center" }); }
          }
        });
      });
    }, 0);
    return svg;
  }

  /* —— 5 决策护照 —— */
  function renderPassport() {
    if (!state.task) { q("tab-passport").innerHTML = "<p class='empty'>先保存需求。</p>"; return; }
    q("tab-passport").innerHTML =
      "<div class='card'><h3>决策护照（Schema v2，绑定同一运行快照）</h3>"
      + (state.run ? "<p class='okline'>当前快照 " + esc(state.run.id) + "。"
        + "下载内容与当前口径（" + (state.optimistic ? "乐观" : "保守") + "）一致。</p>"
        : "<p class='note'>尚未计算：将按当前需求+供给即时生成护照（未绑定运行快照）。</p>")
      + "<div class='form-actions'>"
      + "<button class='primary' id='btn-download-passport'>下载 JSON 护照</button>"
      + "<button id='btn-print'>打印决策护照</button>"
      + "<button id='btn-export-options-csv'>导出方案 CSV（当前快照）</button></div>"
      + "<div id='passport-summary'></div></div>";
    q("btn-download-passport").addEventListener("click", function () {
      var url = "/api/tasks/" + encodeURIComponent(state.task.id) + "/passport"
        + (state.run ? "?run_id=" + encodeURIComponent(state.run.id) : "");
      window.location.href = url;
    });
    q("btn-print").addEventListener("click", function () { window.print(); });
    q("btn-export-options-csv").addEventListener("click", function () {
      if (!state.run) { alert("先运行计算再导出。"); return; }
      var options = (state.run.result.scenarios && state.run.result.scenarios[state.optimistic ? "optimistic" : "conservative"].options) || [];
      var csv = "option_id,kind,label,total_cost_cny,net_avoided_kgco2e,order_max_share,category\n"
        + options.map(function (o) {
          return [o.option_id, o.kind, '"' + String(o.label || "").replace(/"/g, '""') + '"',
            o.total_cost_cny == null ? "" : o.total_cost_cny,
            o.net_avoided_kgco2e == null ? "" : o.net_avoided_kgco2e,
            o.order_max_share == null ? "" : o.order_max_share, o.category].join(",");
        }).join("\n");
      var blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "options_" + state.task.id + "_" + state.run.id + ".csv";
      a.click();
    });
    renderPassportSummary();
  }
  function renderPassportSummary() {
    var box = q("passport-summary");
    if (!box) return;
    if (!state.run) { box.innerHTML = "<p class='empty'>运行计算后可在此查看护照摘要（方案身份/账本/未知项）。</p>"; return; }
    var r = state.run.result;
    var reps = (r.representatives || []);
    box.innerHTML = "<h3>代表方案（" + reps.length + "）</h3>"
      + "<div class='table-scroll'><table><thead><tr><th>方案</th><th>方案ID</th><th>到厂成本 CNY</th>"
      + "<th>净减排 kgCO2e</th><th>分类</th></tr></thead><tbody>"
      + reps.map(function (o) {
        return "<tr><td>" + esc(o.label) + "</td><td>" + esc(o.option_id) + "</td>"
          + "<td>" + fmt(o.total_cost_cny, "cny") + "</td><td>" + fmt(o.net_avoided_kgco2e, "int")
          + "</td><td>" + esc(o.category) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
      + "<h3>未知项（不编造）</h3><p class='empty'>"
      + ((r.passport && r.passport.unknowns || []).map(function (u) {
        return esc(u.supply_id) + "." + esc(u.field);
      }).join("；") || "无") + "</p>"
      + "<p class='empty'>下一步不是立即交易：对「待核验」批次补送样检测；与最低成本供给议价；"
      + "核对基准运输距离假设与各批次实际运输路线。</p>";
  }

  /* —— 6 履约反馈（P1-4/P1-5：结构化字段 + 真实 KPI） —— */
  function renderFeedback() {
    if (!state.task) { q("tab-feedback").innerHTML = "<p class='empty'>先保存需求。</p>"; return; }
    q("tab-feedback").innerHTML =
      "<div class='card'><h3>试点 KPI（真实结构化数据计算；无实证显示「暂无实证」，不生成虚假数字）</h3>"
      + "<div id='kpi-panel'><p class='empty'>加载中…</p></div>"
      + "<h3>履约反馈（现实观察与模拟分开，结构化字段）</h3>"
      + "<div class='feedback-grid'>"
      + "<div><label><b>批次 candidate_id*</b></label><input id='fb-candidate'></div>"
      + "<div><label><b>结论*</b></label><select id='fb-decision'><option>通过</option><option>否决</option><option>补证</option></select></div>"
      + "<div><label><b>观察类型*</b></label><select id='fb-kind'><option value='真实'>真实观察</option>"
      + "<option value='模拟'>模拟（不计入 KPI 实证）</option></select></div>"
      + "<div><label><b>业务阶段</b></label><select id='fb-stage'>"
      + "<option value=''>（未指定）</option><option>送样</option><option>试用</option>"
      + "<option>采购</option><option>交付</option><option>履约完成</option></select></div>"
      + "<div><label><b>日期 (YYYY-MM-DD)*</b></label><input id='fb-date'></div>"
      + "<div><label><b>实际到货量 (t)</b></label><input id='fb-qty' type='number' step='any'></div>"
      + "<div><label><b>实际到货日期</b></label><input id='fb-actual-date' placeholder='YYYY-MM-DD'></div>"
      + "<div><label><b>实际成本 (CNY)</b></label><input id='fb-cost' type='number' step='any'></div>"
      + "<div><label><b>质量结果</b></label><select id='fb-quality'><option value=''>（未指定）</option>"
      + "<option>合格</option><option>偏差可接受</option><option>不合格</option></select></div>"
      + "<div><label><b>退货/违约</b></label><select id='fb-breach'><option value=''>（未指定）</option>"
      + "<option>无</option><option>部分退货</option><option>违约</option></select></div>"
      + "</div>"
      + "<label><b>原因代码</b></label><input id='fb-reason' placeholder='如 熔指偏差/交付延期'>"
      + "<label><b>备注（送样/试用/交付质量·数量·时间/否决原因/成本）</b></label>"
      + "<textarea id='fb-notes' rows='3' style='width:100%'></textarea>"
      + "<div class='form-actions'><button class='primary' id='btn-fb-save'>记录反馈</button>"
      + "<span class='empty' id='fb-msg'></span></div>"
      + "<p class='empty'>采购量、送样通过和实际替代量不能混算成已减排；现实观察与模拟在统计中分开。</p>"
      + "<div id='fb-list'></div></div>";
    q("btn-fb-save").addEventListener("click", async function () {
      var kind = q("fb-kind").value;
      try {
        await API.post("/tasks/" + encodeURIComponent(state.task.id) + "/feedback", {
          candidate_id: q("fb-candidate").value.trim(),
          decision: kind === "真实" ? q("fb-decision").value : "补证",
          reason_code: q("fb-reason").value.trim(),
          reviewed_at: q("fb-date").value.trim(),
          notes: (kind === "模拟" ? "[模拟] " : "") + q("fb-notes").value.trim(),
          scenario_id: state.run ? state.run.id : "",
          rule_version: state.run ? state.run.result.algorithm_version || "" : "",
          observation_type: kind,
          stage: q("fb-stage").value,
          actual_qty_t: q("fb-qty").value,
          actual_date: q("fb-actual-date").value.trim(),
          actual_cost_cny: q("fb-cost").value,
          quality_result: q("fb-quality").value,
          return_breach: q("fb-breach").value,
        });
        q("fb-msg").textContent = "已记录（" + kind + "）。";
        refreshFeedback();
        // 成功后清空表单，避免残留值造成重复提交或串批次记录
        ["fb-candidate", "fb-reason", "fb-date", "fb-qty",
         "fb-actual-date", "fb-cost"].forEach(function (id) { q(id).value = ""; });
        ["fb-decision", "fb-stage", "fb-quality", "fb-breach"]
          .forEach(function (id) { q(id).selectedIndex = 0; });
        q("fb-notes").value = "";
      } catch (e) { q("fb-msg").textContent = "记录失败：" + e.message; }
    });
    refreshFeedback();
  }
  function refreshFeedback() {
    if (!state.task) return;
    API.get("/tasks/" + encodeURIComponent(state.task.id) + "/feedback").then(function (data) {
      var rows = (data.feedback || []).map(function (f) {
        return "<tr><td>" + esc(f.candidate_id) + "</td><td>" + esc(f.decision) + "</td>"
          + "<td><span class='tag " + (f.observation_type === "模拟" ? "info" : "ok") + "'>"
          + esc(f.observation_type) + "</span></td><td>" + esc(f.stage || "-") + "</td>"
          + "<td>" + esc(f.reviewed_at) + "</td>"
          + "<td>" + (f.actual_qty_t != null ? f.actual_qty_t + " t" : "-") + "</td>"
          + "<td>" + (f.actual_cost_cny != null ? f.actual_cost_cny : "-") + "</td>"
          + "<td>" + esc(f.quality_result || "-") + "</td>"
          + "<td>" + esc(f.return_breach || "-") + "</td>"
          + "<td>" + esc(f.notes || "-") + "</td></tr>";
      }).join("");
      q("fb-list").innerHTML = rows
        ? "<h3>历史反馈（" + (data.feedback || []).length + " 条）</h3><div class='table-scroll'><table>"
          + "<thead><tr><th>批次</th><th>结论</th><th>类型</th><th>阶段</th><th>日期</th>"
          + "<th>到货量 t</th><th>实际成本</th><th>质量</th><th>退货/违约</th><th>备注</th></tr></thead><tbody>"
          + rows + "</tbody></table></div>"
        : "<p class='empty'>暂无反馈记录。</p>";
      renderKpis(data.feedback || []);
    }).catch(function () { });
  }
  function renderKpis(feedback) {
    // P1-5：只统计「真实」观察；样本不足如实显示
    var real = feedback.filter(function (f) { return f.observation_type === "真实"; });
    var supplied = real.filter(function (f) { return f.stage === "采购" || f.stage === "交付" || f.stage === "履约完成"; });
    var kpis = [];
    function kpi(name, value, note) {
      kpis.push("<div class='kpi'><div class='num'>" + value + "</div><div class='cap'>" + name
        + (note ? "（" + note + "）" : "") + "</div></div>");
    }
    if (state.run && state.run.result.match_states) {
      var ms = state.run.result.match_states;
      kpi("有效匹配率（当前快照）", ms.length ? Math.round(ms.filter(function (s) { return s.state === "eligible"; }).length / ms.length * 100) + "%" : "—",
          "可比较批次/全部批次");
    }
    kpi("证据补齐周期", real.length >= 1 ? "样本不足" : "暂无实证",
        "证据任务创建→通过 天数中位数，样本≥5 才显示");
    kpi("确认后履约率", supplied.length ? Math.round(supplied.filter(function (f) { return f.decision === "通过" && f.return_breach === "无" || f.return_breach === ""; }).length / supplied.length * 100) + "%" : "暂无实证",
        "采购/交付/履约完成阶段「通过且无违约」/条数");
    kpi("实际成本偏差", supplied.filter(function (f) { return f.actual_cost_cny != null; }).length ? "样本不足" : "暂无实证",
        "（实际−计划）/计划，需确认方案账本对比");
    kpi("质量异常率", real.length ? Math.round(real.filter(function (f) { return f.quality_result === "不合格" || f.return_breach === "违约"; }).length / real.length * 100) + "%" : "暂无实证",
        "不合格/违约条数/真实观察条数");
    kpi("可核证减排量", "暂无实证",
        "需实测排放或代理指标，见交付模板口径");
    q("kpi-panel").innerHTML = "<div class='grid'>" + kpis.join("") + "</div>";
  }

  /* —— 管理面板（仅管理员） —— */
  function renderAdminPanel() {
    if (!state.me || state.me.role !== "admin") { q("admin-panel").innerHTML = ""; return; }
    q("admin-panel").innerHTML =
      "<div class='card'><h3>管理：用户与备份（仅管理员可见）</h3>"
      + "<div class='field-row'>"
      + "<div><label>新用户名</label><input id='adm-username'></div>"
      + "<div><label>密码（≥8 位）</label><input id='adm-password' type='password'></div>"
      + "<div><label>角色</label><select id='adm-role'><option value='editor'>编辑</option>"
      + "<option value='viewer'>只读</option><option value='admin'>管理员</option></select></div>"
      + "</div><div class='form-actions'>"
      + "<button id='btn-create-user'>创建账号</button>"
      + "<button class='primary' id='btn-backup'>创建备份</button>"
      + "<button id='btn-list-backups'>查看备份列表</button>"
      + "<button class='danger' id='btn-restore-last'>恢复最近备份（危险）</button>"
      + "</div><div id='admin-result'>" + (state.adminMessage || "") + "</div></div>";
    function setAdmin(msg) { state.adminMessage = msg; var box = q("admin-result"); if (box) box.innerHTML = msg; }
    q("btn-create-user").addEventListener("click", function () {
      API.post("/users", { username: q("adm-username").value.trim(),
        password: q("adm-password").value, role: q("adm-role").value })
        .then(function () { setAdmin("<div class='okline'>账号已创建。</div>"); })
        .catch(function (e) { setAdmin("<div class='error'>" + esc(e.message) + "</div>"); });
    });
    q("btn-backup").addEventListener("click", function () {
      API.post("/backup").then(function (res) {
        setAdmin("<div class='okline'>备份完成：" + esc(res.backup) + "</div>");
      }).catch(function (e) { setAdmin("<div class='error'>" + esc(e.message) + "</div>"); });
    });
    q("btn-list-backups").addEventListener("click", function () {
      API.get("/backups").then(function (res) {
        setAdmin("<div class='table-scroll'><table><thead><tr><th>备份</th><th>大小</th>"
          + "<th>操作</th></tr></thead><tbody>" + res.backups.map(function (b) {
            return "<tr><td>" + esc(b.name) + "</td><td>" + b.size + " B</td>"
              + "<td><button data-restore='" + esc(b.name) + "'>恢复</button></td></tr>";
          }).join("") + "</tbody></table></div>");
        q("admin-result").querySelectorAll("[data-restore]").forEach(function (b) {
          b.addEventListener("click", function () {
            if (!confirm("恢复备份 " + b.dataset.restore + " 将覆盖当前数据，确认？")) return;
            API.post("/backups/" + encodeURIComponent(b.dataset.restore) + "/restore")
              .then(function () { setAdmin("<div class='okline'>已恢复。</div>"); return refreshTasks(); })
              .catch(function (e) { setAdmin("<div class='error'>" + esc(e.message) + "</div>"); });
          });
        });
      }).catch(function (e) { setAdmin("<div class='error'>" + esc(e.message) + "</div>"); });
    });
    q("btn-restore-last").addEventListener("click", function () {
      API.get("/backups").then(function (res) {
        var last = res.backups[0];  // 服务端按名称降序返回（最新在前）
        if (!last) { setAdmin("<div class='error'>没有备份。</div>"); return; }
        if (!confirm("将恢复到最近备份 " + last.name + "，覆盖当前全部数据，确认？")) return;
        return API.post("/backups/" + encodeURIComponent(last.name) + "/restore");
      }).then(function () { setAdmin("<div class='okline'>已恢复最近备份。</div>"); })
        .catch(function (e) { setAdmin("<div class='error'>" + esc(e.message) + "</div>"); });
    });
  }

  /* —— 多需求撮合（只读试算，v16 任务 3；建议单导出 v17 任务 2） —— */
  var lastMatchingIds = null;  // 最近一次成功试算的勾选（建议单导出用同入参）
  function openMatching() {
    q("matching-results").innerHTML = "";
    q("matching-hint").textContent = "";
    lastMatchingIds = null;
    var isViewer = state.me && state.me.role === "viewer";
    var runBtn = q("btn-matching-run");
    runBtn.disabled = isViewer;
    runBtn.title = isViewer ? "只读账号无撮合权限" : "";
    if (isViewer) q("matching-hint").textContent =
      "只读账号不能计算撮合建议（服务端同样拒绝：403）。";
    var html = state.tasks.length
      ? "<div class='table-scroll'><table><thead><tr><th>选</th><th>任务</th><th>case_id</th>"
        + "<th>需求</th><th>供给批次</th></tr></thead><tbody>"
        + state.tasks.map(function (t) {
            var d = t.demand || {};
            return "<tr><td><input type='checkbox' class='matching-pick' data-tid='"
              + esc(t.id) + "'></td>"
              + "<td>" + esc(t.id) + "</td><td>" + esc(t.case_id) + "</td>"
              + "<td>" + esc(d.target_material_code || "") + " "
              + fmt(d.required_mass_t, "t") + " t · " + esc(d.application || "") + "</td>"
              + "<td>" + fmt(t.supply_count, "int") + "</td></tr>";
          }).join("") + "</tbody></table></div>"
      : "<p class='empty'>暂无任务：先在任务列表创建替代任务并录入供给批次。</p>";
    q("matching-task-list").innerHTML = html;
    show("matching");
  }
  q("btn-matching").addEventListener("click", function () {
    refreshTasks().then(openMatching)
      .catch(function (e) { alert("打开撮合页失败：" + e.message); });
  });
  q("btn-matching-back").addEventListener("click", function () {
    show("tasks"); refreshTasks();
  });
  q("btn-matching-run").addEventListener("click", async function () {
    var ids = Array.prototype.map.call(
      document.querySelectorAll(".matching-pick:checked"), function (c) {
        return c.dataset.tid; });
    var hint = q("matching-hint");
    var btn = q("btn-matching-run");
    if (!ids.length) { hint.textContent = "请先勾选至少 1 个任务（建议 2 个以上以体现跨任务撮合）。"; return; }
    if (ids.length > 50) { hint.textContent = "单次最多勾选 50 个任务。"; return; }
    if (btn.disabled) return;  // 防重复提交：计算期间按钮不可点
    btn.disabled = true;
    btn.classList.add("busy");
    hint.textContent = "计算中…（即算即返，不落库）";
    q("matching-results").innerHTML = "";
    try {
      var data = await API.post("/matching/preview", { task_ids: ids });
      hint.textContent = "完成，耗时 " + data.elapsed_ms + " ms。";
      lastMatchingIds = ids;
      renderMatchingResults(data);
    } catch (e) {
      hint.textContent = "";
      q("matching-results").innerHTML =
        "<div class='card'><div class='error'>撮合失败：" + esc(e.message) + "</div></div>";
    } finally {
      btn.disabled = false;
      btn.classList.remove("busy");
    }
  });

  function renderMatchingResults(data) {
    var p = data.preview || {};
    var matches = p.matches || [];
    var unmatched = p.unmatched || [];
    var reasons = data.reasons || {};
    var stable = p.stability && p.stability.blocking_pairs === 0;
    var badge = stable
      ? "<span class='tag' style='border-color:#1e6b49;color:#1e6b49'>● 稳定性自检通过（阻断对 0）</span>"
      : "<span class='tag' style='border-color:#8b2f2f;color:#8b2f2f'>● 稳定性自检失败（阻断对 "
        + fmt(p.stability && p.stability.blocking_pairs, "int") + "）——实现缺陷，请勿采用</span>";
    var html = "<div class='card'><h3>撮合汇总</h3><p>"
      + "参与需求 " + fmt((data.demands || []).length, "int")
      + " · 匹配需求 " + fmt(matches.length, "int")
      + " · 未满足需求 " + fmt(unmatched.length, "int")
      + " · 批次 " + fmt((data.batches || []).length, "int") + " " + badge + "</p>"
      + "<p class='empty'>" + (data.disclaimers || []).map(esc).join("；") + "</p>"
      + "<div class='form-actions'>"
      + "<button class='primary' id='btn-matching-export'>导出建议单（HTML）</button>"
      + "<span class='empty'>建议单由服务端按同入参即时重算渲染（只读不落库），"
      + "内容与本页数值同源；文件是否保存由你在浏览器决定。</span></div></div>";
    if (matches.length) {
      html += "<div class='card'><h3>匹配明细</h3><div class='table-scroll'><table><thead><tr>"
        + "<th>需求任务</th><th>需求量 t</th><th>满足 t</th><th>未满足 t</th>"
        + "<th>分配明细（批次 × t）</th><th>建议到厂成本 CNY</th><th>撮合理由</th></tr></thead><tbody>"
        + matches.map(function (m) {
            var dm = (data.demands || []).filter(function (d) {
              return d.task_id === m.demand_id; })[0] || {};
            var alloc = Object.keys(m.allocations || {}).sort().map(function (bid) {
              return esc(bid) + " × " + fmt(m.allocations[bid], "t"); }).join("；");
            return "<tr><td>" + esc(m.demand_id)
              + (dm.buyer_name ? "（" + esc(dm.buyer_name) + "）" : "")
              + "</td><td>" + fmt(dm.required_mass_t, "t") + "</td>"
              + "<td>" + fmt(m.satisfied_t, "t") + "</td>"
              + "<td>" + fmt(m.remaining_t, "t") + "</td>"
              + "<td>" + alloc + "</td>"
              + "<td>" + fmt(m.proposed_cost_cny, "cny") + "</td>"
              + "<td>" + esc(reasons[m.demand_id] || "") + "</td></tr>";
          }).join("") + "</tbody></table></div>"
        + "<p class='empty'>建议成本为演示模型核算口径（材料+运费+装卸+检测+损耗），非报价；"
        + "撮合建议（非交易/非预留）。</p></div>";
    }
    if (unmatched.length) {
      html += "<div class='card'><h3>未满足需求与原因</h3><div class='table-scroll'><table><thead><tr>"
        + "<th>需求任务</th><th>未满足量 t</th><th>原因</th></tr></thead><tbody>"
        + unmatched.map(function (u) {
            return "<tr><td>" + esc(u.demand_id) + "</td><td>" + fmt(u.remaining_t, "t")
              + "</td><td>" + esc(u.reason) + "</td></tr>";
          }).join("") + "</tbody></table></div></div>";
    }
    if (p.leftover_capacity_t && Object.keys(p.leftover_capacity_t).length) {
      html += "<div class='card'><h3>批次剩余容量（本轮撮合后，未占用）</h3>"
        + "<div class='table-scroll'><table><thead><tr><th>批次</th><th>剩余容量 t</th>"
        + "</tr></thead><tbody>"
        + Object.keys(p.leftover_capacity_t).sort().map(function (bid) {
            return "<tr><td>" + esc(bid) + "</td><td>"
              + fmt(p.leftover_capacity_t[bid], "t") + "</td></tr>";
          }).join("") + "</tbody></table></div>"
        + "<p class='empty'>剩余容量为撮合试算的静态余量展示，不构成任何预留或占用。</p></div>";
    }
    if (data.svg) {
      html += "<div class='card'><h3>双边关系视图</h3>" + data.svg + "</div>";
    }
    q("matching-results").innerHTML = html;
    var exportBtn = q("btn-matching-export");
    if (exportBtn) exportBtn.addEventListener("click", async function () {
      if (!lastMatchingIds || !lastMatchingIds.length) return;
      if (exportBtn.disabled) return;  // 防重复点击导致重复下载
      exportBtn.disabled = true;
      exportBtn.classList.add("busy");
      try {
        var res = await fetch("/api/matching/sheet", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": API.csrf },
          body: JSON.stringify({ task_ids: lastMatchingIds }) });
        if (!res.ok) {
          var err = null;
          try { err = await res.json(); } catch (e) { /* 非 JSON 错误体 */ }
          throw new Error((err && err.error) || ("请求失败 " + res.status));
        }
        var text = await res.text();
        var stamp = new Date();
        var pad = function (n) { return (n < 10 ? "0" : "") + n; };
        var name = "撮合建议单_" + lastMatchingIds.join("+") + "_"
          + stamp.getFullYear() + pad(stamp.getMonth() + 1) + pad(stamp.getDate())
          + "-" + pad(stamp.getHours()) + pad(stamp.getMinutes()) + ".html";
        var blob = new Blob([text], { type: "text/html;charset=utf-8" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);
      } catch (e) {
        alert("导出建议单失败：" + e.message);
      } finally {
        exportBtn.disabled = false;
        exportBtn.classList.remove("busy");
      }
    });
  }

  /* —— 快捷键不拦截输入框；空状态/失败状态如实显示 —— */
  document.addEventListener("keydown", function (e) {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA"
        || e.target.tagName === "SELECT" || e.target.isContentEditable)) return;
    if (e.key === "Escape" && state.task) { show("tasks"); refreshTasks(); }
  });

  initAuth();
})();
