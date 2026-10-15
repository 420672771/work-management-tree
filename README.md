# 工作管理树（V1.2 MVP）

面向科技产品中心的责任树与任务闭环系统：责任树描述长期责任边界，任务只有一个最终责任人，责任人自行组织内部协同。

## 本地运行

```bash
cd /home/admin/work-management-tree
python3 app.py
```

打开 http://127.0.0.1:8080 。数据库默认写入 `data/worktree.db`，可用 `WMT_DB` 覆盖。

也可用可重复启停脚本部署：

```bash
chmod +x scripts/*.sh
./scripts/start-local.sh
./scripts/healthcheck.sh
./scripts/stop-local.sh
```

临期/超期提醒可由本地计划任务执行（同一天、同一任务、同一提醒类型幂等）：

```bash
python3 scripts/send-reminders.py
```

## 已实现

- 责任域/责任事项树
- 任务创建、唯一责任人、接单、进度反馈、完成提交、验收/驳回
- 工作状态、时间状态、风险状态分离
- 临期/超期看板
- AI 任务草稿（规则解析适配器，后续可接模型）
- 企业微信消息适配器（默认 dry-run，配置凭据后启用）
- 审计日志

## 企业微信配置

设置 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET` 后，系统可通过 `/api/notify` 发送应用消息；未配置时只记录 dry-run 日志，不会误发消息。设置 `WECOM_TOKEN` 和 43 位 `WECOM_AES_KEY` 后，将企业微信应用“接收消息” URL 配置为 `/wecom/callback`，员工可发送 `任务#123 已完成/预计周五` 回写任务反馈。

AI 草稿默认使用规则解析。若配置兼容 OpenAI Chat Completions 的 `AI_API_BASE`、`AI_API_KEY`、`AI_MODEL`（例如企业批准的模型服务），则优先调用模型；模型错误时会安全回退为规则草稿，且始终要求 PM 人工确认。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

查看真实企业微信接入前置条件：

```bash
python3 scripts/readiness-check.py
```

## 无凭据演示

可用隔离数据库先验证完整流程：

```bash
WMT_DB=/tmp/worktree-demo.db python3 scripts/bootstrap-demo.py
WMT_DB=/tmp/worktree-demo.db PORT=8090 python3 app.py
```

运行完整的业务闭环验收：

```bash
WMT_DB=/tmp/worktree-pilot.db python3 scripts/run-demo-pilot.py
```

默认服务只绑定本机使用；若要让企业微信回调访问，必须部署到企业内网可达地址或使用经过批准的安全网关。

公网/临时隧道发布前必须设置 `WMT_PUBLIC_USER` 和 `WMT_PUBLIC_PASSWORD`，否则不要暴露端口。当前服务提供可选 HTTP Basic Auth；正式生产建议放在 HTTPS 反向代理和企业身份认证之后。

如使用 Cloudflare Quick Tunnel：先启动带认证的本地服务，再运行 `scripts/start-cloudflare-preview.sh`。Quick Tunnel 适合临时验证，无固定域名和 uptime 保证。
