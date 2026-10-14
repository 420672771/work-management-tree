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

## 已实现

- 责任域/责任事项树
- 任务创建、唯一责任人、接单、进度反馈、完成提交、验收/驳回
- 工作状态、时间状态、风险状态分离
- 临期/超期看板
- AI 任务草稿（规则解析适配器，后续可接模型）
- 企业微信消息适配器（默认 dry-run，配置凭据后启用）
- 审计日志

## 企业微信配置

设置 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET` 后，系统可通过 `/api/notify` 发送应用消息；未配置时只记录 dry-run 日志，不会误发消息。

AI 草稿默认使用规则解析。若配置兼容 OpenAI Chat Completions 的 `AI_API_BASE`、`AI_API_KEY`、`AI_MODEL`（例如企业批准的模型服务），则优先调用模型；模型错误时会安全回退为规则草稿，且始终要求 PM 人工确认。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
