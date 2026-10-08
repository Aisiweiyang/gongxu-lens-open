# 试点部署指南 · Linux（v20）

生成：2026-09-14（SPEC-v20 任务 2）。适用：Ubuntu 22.04/24.04、Debian 12 等
主流发行版。**验证口径（如实）**：本系统在 WSL2（Ubuntu 用户态，Linux 6.18，
Python 3.14）长期运行与测试；裸机 Linux 未实测——步骤为标准 Linux 操作，
如有发行版差异按其 Python 文档调整。推荐 Linux 的依据：实测引擎性能约为
Windows 的 2–3 倍（docs/压力基准-v16.md）。

## 一、前置条件

```bash
python3 --version          # 3.12+（本项目开发验证于 3.12 与 3.14）
python3 -m venv --help >/dev/null || sudo apt install python3-venv
```

## 二、安装与启动（约 5 分钟）

```bash
# 1. 解压/拷入发行包（dist/pilot-app/，含 FILES.txt SHA-256 清单）
cd ~/gongxu-lens
sha256sum -c FILES.txt --quiet && echo "包完整性 OK"   # 可选：核对发行包

# 2. 虚拟环境（零第三方运行依赖，无需 pip install）
python3 -m venv .venv
.venv/bin/python pilot/init_admin.py --help   # 可选：命令行初始化管理员

# 3. 启动（默认 127.0.0.1:8765，数据落 data/pilot.sqlite3）
.venv/bin/python pilot/start_pilot.py --port 8765

# 4. 自检（v20 新增）
.venv/bin/python scripts/pilot_selfcheck.py --port 8765
```

浏览器打开 `http://<部署机IP>:8765/`（局域网访问需 `--host 0.0.0.0` 启动，
并建议按第六节加反向代理）。

## 三、数据与备份

- 全部业务数据在 `pilot/data/pilot.sqlite3`（单文件）＋ `pilot/backups/`（内置备份）。
- 管理员登录 → 管理面板 → 创建备份（含 SHA-256 清单）；恢复带二次确认。
- 建议策略：试点期每日备份一次＋每周离机拷贝一次（`cp data/backups/<最新> `
  到外部介质）。系统不自动外发任何数据。

## 四、开机自启（可选，systemd）

`/etc/systemd/system/gongxu-lens.service`：

```ini
[Unit]
Description=SupplyLens pilot
After=network.target

[Service]
User=<部署用户>
WorkingDirectory=/home/<部署用户>/gongxu-lens
ExecStart=/home/<部署用户>/gongxu-lens/.venv/bin/python pilot/start_pilot.py --port 8765
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now gongxu-lens
systemctl status gongxu-lens
```

注意（与 ADR-0006 一致）：重启会丢失**进行中的后台计算作业**（需重新提交），
已完成的结果与全部业务数据在 SQLite 中不受影响。

## 五、常见问题

| 症状 | 处置 |
|---|---|
| 端口被占 | `--port` 换端口，或 `ss -ltnp` 找占用进程 |
| 局域网访问不了 | 确认以 `--host 0.0.0.0` 启动且防火墙放行端口 |
| 引擎超限场景提示贪心回退 | 未装 OR-Tools（可选依赖）：`pip install ortools==9.15.6755`；不装则如实按回退口径使用 |
| 登录 429 | 同 IP 900 秒 10 次限流（防暴力破解设计），等待窗口或错峰 |
| 忘记管理员口令 | 删除 `data/pilot.sqlite3` 中 sessions 后按手册口令重置流程操作；或恢复最近备份（先咨询交付支持） |

## 六、局域网安全建议（如实）

1. 服务为纯 HTTP：建议经反向代理（nginx/caddy）加 TLS 后再放开局域网；
2. 首次启动后立即设置强管理员口令，并按需创建 editor/viewer 账号分权；
3. 定期查看管理面板审计记录；
4. 公网直接暴露不在支持范围（见 docs/安全与合规应答清单-v20.md 第六节）。

## 七、验收清单（部署完成后逐项勾选）

- [ ] `scripts/pilot_selfcheck.py` 全项通过
- [ ] 管理员初始化并登录成功
- [ ] 演示样例导入（`pilot/import_sample.py`）并完成一次运行计算
- [ ] 创建一个备份并能在管理面板看到
- [ ] 第二台设备经局域网可访问（如需多用户）
