# 企业微信配置与试点操作

1. 在企业微信创建自建应用，配置 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET`。
2. 在应用“接收消息”中生成 Token 和 EncodingAESKey，配置 `WECOM_TOKEN`、`WECOM_AES_KEY`。
3. 回调 URL 指向 `/wecom/callback`，先用测试账号完成 GET 验证。
4. 通过 `/api/users` 为每位责任人录入 `name`、`department`、`role`、`wecom_userid`。
5. 每日执行 `scripts/send-reminders.py`；未配置发送凭据时为 dry-run，不会外发。
6. 员工回复 `任务#编号 + 进度/风险`，系统自动保存任务反馈。

本地服务若无法被企业微信访问，需要在企业内网部署，或使用经批准的内网穿透方案；不得将 Secret、Token 或 AESKey 写入 Git。
