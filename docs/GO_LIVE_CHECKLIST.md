# V1.2 上线门禁

## A. 业务门禁

- [ ] 决策人确认科技产品中心首批责任域
- [ ] 每个责任事项都有唯一责任人
- [ ] 每个试点任务都有截止时间和交付/验收标准
- [ ] PM 明确每日维护责任和例会入树流程
- [ ] 员工知道反馈格式：`任务#编号 + 进度/风险`

## B. 技术门禁

- [ ] `python3 -m unittest discover -s tests -v` 全部通过
- [ ] `./scripts/start-local.sh` 可启动
- [ ] `./scripts/healthcheck.sh` 返回 dashboard
- [ ] 数据库备份和恢复已演练
- [ ] 真实企业微信发送已用测试账号验证
- [ ] 回调 GET 验证和 POST 解密已验证
- [ ] 日志不包含 Secret、Token 和 AESKey
- [ ] 临期/超期提醒重复执行不重复发送
- [ ] `scripts/run-demo-pilot.py` 全部检查通过
- [ ] `scripts/readiness-check.py` 报告 `ready_for_real_wecom: true`

## C. 试点门禁

- [ ] 至少 10 个真实任务完成创建、接单、反馈、验收
- [ ] 试点连续运行 5 个工作日
- [ ] 责任人接单率 ≥ 90%
- [ ] 任务验收率 ≥ 95%
- [ ] 逾期升级记录覆盖率 = 100%
- [ ] PM 和决策人完成周复盘

任何一项 P0 技术门禁未通过，不进入真实员工群发阶段。
