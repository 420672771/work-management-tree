# 正式基础资料包

本目录的 `production-base-20261109.wmt` 是可随 Git 拉取的完整加密迁移包，使用 AES-256-GCM 加密并校验。仓库为公开仓库，密码哈希、飞书绑定和实际组织人员资料均在密文中，不能将原始数据库或解密密钥提交到 Git。

本包包含 17 名正式员工及账号、6 个岗位、18 条任职记录、16 条汇报/确认关系、4 个部门、1 个责任树根节点及 5 个责任域。排除例行演练人员、岗位及测试模板，不含任务和历史业务数据。原员工密码哈希及当前飞书绑定保留。

解密密钥通过独立渠道交付运维。以下命令在生产项目根目录执行，依赖先安装 `requirements.txt`。省略 `--key-file` 时会在终端隐藏输入密钥；不要把密钥直接放进命令参数或 `.env` 示例。

```bash
git pull origin main
python3 scripts/production-package.py inspect \
  --package deploy/migrations/production-base-20261109.wmt \
  --key-file /secure/path/production-base-20261109.key

python3 scripts/production-package.py install \
  --package deploy/migrations/production-base-20261109.wmt \
  --key-file /secure/path/production-base-20261109.key \
  --database ./data/worktree-migrated.db
```

`inspect` 解密检查包内校验值、SQLite 完整性、外键和各表实际数量；只输出数量，不显示密码或人员详情。`install` 再次检查后安装为新的候选数据库，权限为 `600`。目标或其 WAL/SHM 已存在时拒绝覆盖；不会自行切换网站或飞书服务。

确认生产没有需要保留的新业务、账号和绑定后，停止网站、飞书接收服务及定时任务，使用 SQLite 备份接口备份当前生产数据库，再按实际部署方式将所有服务切换到候选数据库。若已有正式业务，则需先合并基础资料，不能用初始化包整库替换。迁移后使用原正式员工账号，当前空库单独建立的 `admin` 账号不会随包保留。

- Docker Compose：`./data/worktree-migrated.db` 对应容器 `/app/data/worktree-migrated.db`。项目 Compose 的 `WMT_DB` 当前固定为 `/app/data/worktree.db`；使用候选文件时须修改实际运行配置，并让飞书服务及定时任务使用同一个文件。不要只修改 `.env` 中被 Compose 显式覆盖的值。
- systemd / 直接 Python：设置进程实际 `WMT_DB` 的绝对路径。直接运行 Python 不会自动加载 `.env`。
- 数据包放进仓库目录不会自动导入。生产页面应显示 6 个岗位、17 名正式员工；“0 个岗位、7 名默认成员”意味着仍需核对数据库路径和挂载。

完整迁移范围及回退说明见[生产发布与基础资料迁移](../../docs/生产发布与基础资料迁移.md)。解密后的数据库、校验报告、密钥文件仍保存在被 Git 忽略的私有目录中。
