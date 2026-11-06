# 正式公网部署

本项目可以运行在本地服务器上，但“本地运行”不等于“互联网可访问”。正式公网访问必须具备以下任一条件：

1. 服务器有可入站的公网 IPv4，并在防火墙/路由器放行 443（必要时将 443 转发到本机）；
2. 使用 Cloudflare Named Tunnel（固定账号、固定 tunnel、固定域名）；
3. 将应用部署到一台有公网 IP 的 VPS/云服务器。

不要使用 `trycloudflare.com` Quick Tunnel 或 LocalTunnel 作为生产入口，它们没有固定连接和可用性保证，连接进程退出就会出现 1033/503。

## 测试服务一次启用自动重载

当前 `worktree.service` 使用常驻 Python 进程，后端修改需要重新加载。测试阶段可在服务器上以运行该服务的用户执行一次：

```bash
bash /home/admin/work-management-tree/scripts/configure-auto-reload.sh
```

脚本先检查服务访问权限和源码，再启用项目本地开关并重启一次。原 systemd unit、5100 端口、数据库及认证环境配置保持不变。以后后端 Python 文件修改并稳定 2 秒后，会先进行语法及隔离导入检查，通过后自动重载；检查失败时保留原进程。页面、CSS、JavaScript 修改无需重载后端，刷新浏览器即可。

此模式适合当前试点开发，重载期间会有短暂中断；正式上线建议关闭自动重载，验证后统一发布。关闭命令：

```bash
bash /home/admin/work-management-tree/scripts/configure-auto-reload.sh --disable
```

源码检查也可单独执行，不会启动服务：

```bash
python3 /home/admin/work-management-tree/scripts/run-dev-server.py --check
```

该开关默认关闭，不监听数据库、附件、前端资源或测试文件。启用脚本须在有权管理服务的服务器终端执行，不能由受限执行环境代替 systemd 授权。

## 部门周报定时生成

应用启动时自动建立周报表。部署新版后重启应用，使新接口与页面生效；数据库仍使用 `WMT_DB` 指定的路径。

本机用户级 systemd 定时器配置如下：

```bash
systemctl --user link /home/admin/work-management-tree/deploy/worktree-weekly-report.service
systemctl --user link /home/admin/work-management-tree/deploy/worktree-weekly-report.timer
systemctl --user daemon-reload
systemctl --user enable --now worktree-weekly-report.timer
systemctl --user list-timers worktree-weekly-report.timer --no-pager
```

定时器明确使用 `Asia/Shanghai`，默认周日 18:00 执行。`Persistent=true` 支持机器恢复后的补生成。部署在其他目录时同步调整 service 中的目录；若网站使用自定义数据库，须让此 service 使用相同的 `WMT_DB`。也可通过 `scripts/install-daily-timers.sh` 安装全部现有定时任务和周报任务。

服务执行 `scripts/generate-weekly-report.py`，生成待发布快照并记入审计日志，不自动发布、不发送外部消息。若本周草稿已提前补生成，则更新其任务快照并保留人工内容；已发布版本保持不变。同一周期重复执行不会覆盖编辑内容。负责人在首页收到提示后查看并发布，日常编辑自动保存；任务数据更新位于页面的“更多操作”。

## 例行任务按周生成与工时

“组织配置 → 例行任务模板”配置必填的每周标准工时（0.5—168 小时）。旧模板须编辑补齐，未配置工时的模板跳过生成，不使用默认工时。旧的每日模板转为每周模板，已有任务保持不变。

```bash
systemctl --user link /home/admin/work-management-tree/deploy/worktree-routines.service
systemctl --user link /home/admin/work-management-tree/deploy/worktree-routines.timer
systemctl --user daemon-reload
systemctl --user enable --now worktree-routines.timer
systemctl --user list-timers worktree-routines.timer --no-pager
```

定时器每周日 18:00（Asia/Shanghai）生成下一周周一至周日的任务，立即正式生效、状态为待开始，不建立任务确认审批。任务截止为该周日 23:59:59。负责人验收完成的流程保持不变。模板与周起始日期保证唯一，并发执行也不会重复生成；改模板仅影响未来任务，当前任务保留标准工时快照。

实际工时在任务卡或任务详情点击“填写本周工时”，填入该周累计投入（0—168 小时）；保存替换原值，不累加、不自动改变任务状态。责任人、部门负责人和管理 PM 可操作，真实登录身份优先于提交的演示身份。

定时器恢复时只补最近一次到期周期，不批量补历史空周。命令 `python3 scripts/generate-routines.py --week 2026-11-09` 可人工补指定日期所在周（不会发送外部消息）。与其他定时任务一样，自定义数据库时须通过 service 的 `Environment=WMT_DB=...` 指向网站相同数据库；更换部署目录也须同步修改 service 路径。

模板新增、编辑与手动补生成限部门负责人或管理 PM；真实登录身份优先于请求中的演示身份。人工补生成 API 可传 `template_ids` 限定模板，定时任务仍处理全部生效且已配置工时的模板。已生成周任务的标准工时为只读快照。例行任务的周工时填报（包括明确填报 0 小时）计入绩效过程记录，100% 进度确认进入待验收，验收通过后才形成已交付成果。

### 保留的例行任务全流程演练

`scripts/test-routine-business-flow.py` 按 2026 年 11 月业务周期演练模板配置、按周生成、执行、工时、验收、逾期、跨期和绩效，使用 `routine.*` 独立账号、`[例行演练]` 模板和任务。先使用 SQLite 备份接口制作隔离副本，再执行：

```bash
python3 scripts/test-routine-business-flow.py --database /tmp/isolated-worktree.db --output /tmp/routine-flow-report
```

明确需要在测试站保留数据时，增加 `--retain` 并指定网站数据库；脚本拒绝覆盖已有同批演练账号。演练只补生成指定模板，不改其他任务；演练账号没有飞书绑定，运行进程也关闭外部消息凭据。既有员工、账号、任务、模板逐条核对。四类主要模板继续生效，兼容性和删除验证模板停用保留。生产迁移应排除 `routine.*` 演练账号及本批关联记录。

本次保留的结果位于 `artifacts/routine-flow-test/manifest.json` 和 `report.html`，网站入口 `/routine-test-report`。真实登录页面核查脚本为 `tests/routine-business.browser.cjs`，要求显式设置 `WMT_ROUTINE_TEST_URL`、`WMT_ROUTINE_MANIFEST`、`WMT_PLAYWRIGHT_MODULE`；该脚本只读取演练业务数据、打开后取消编辑，并退出自己创建的登录会话。

## VPS/公网主机

将项目放到 `/opt/work-management-tree`，创建 `worktree` 用户，把 `worktree.service` 安装到 `/etc/systemd/system/`，再执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now worktree
sudo systemctl status worktree
```

用 Caddy/Nginx 终止 HTTPS，并将域名反代到 `127.0.0.1:8080`。公网暴露前必须在 `/etc/worktree/worktree.env` 设置 `WMT_PUBLIC_USER` 与 `WMT_PUBLIC_PASSWORD`，并使用强随机密码。

## Cloudflare Named Tunnel

在 Cloudflare 账号中创建固定 tunnel 和 DNS hostname，将 `cloudflared-config.yml` 中的 UUID、凭据路径、域名替换后安装为 systemd 服务。Named Tunnel 的 connector 由 systemd 自动重启；不要再次生成 Quick Tunnel 链接。

## 测试环境个人月报身份切换

在测试服务环境中设置 `WMT_AUTH_MODE=demo` 和 `WMT_MONTHLY_DEMO=1`，重启服务使环境变量生效。页面顶部的演示身份即可用于月报全部操作，保存到当前测试数据库；切换员工前会完成最后一次自动保存，保存失败时保持原身份。

当前用户级服务可在 `~/.config/systemd/user/worktree.service.d/monthly-demo.conf` 中配置：

```ini
[Service]
Environment=WMT_AUTH_MODE=demo
Environment=WMT_MONTHLY_DEMO=1
```

执行 `systemctl --user daemon-reload` 和 `systemctl --user restart worktree.service` 后刷新页面。正式部署使用 `WMT_MONTHLY_DEMO=0`、`WMT_AUTH_MODE=feishu` 并完成飞书账号绑定；无需改动月报代码即可恢复真实登录隔离。飞书登录模式始终忽略演示开关和提交的演示身份，已有登录会话也始终优先于演示身份。


## 账号密码与飞书登录

先在测试模式的员工管理中配置负责人账号，或使用 `WMT_DB=<网站数据库> python3 scripts/set-user-account.py --user-id <ID> --username <账号>` 在服务器终端配置。随后将服务环境设置为 `WMT_AUTH_MODE=account`、`WMT_MONTHLY_DEMO=0`，重新加载服务环境，即可要求员工通过 `/login` 登录。`WMT_AUTH_MODE=feishu` 也兼容账号密码登录。

飞书扫码登录和个人中心扫码绑定共用 `WMT_FEISHU_OAUTH_REDIRECT_URI`，或由 `WMT_PUBLIC_BASE_URL` 加 `/auth/feishu/callback` 构成回调地址。需在飞书应用后台登记完全一致的 URL，并配置现有 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。缺少回调配置时，账号密码功能可正常运行，飞书扫码入口会提示配置状态。测试环境无需强制切换登录模式，可继续保留演示身份。
