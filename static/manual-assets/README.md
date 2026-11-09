# 用户手册配图

正式用户手册引用 16 张 `screen-*.png` 实际界面截图、6 张 `flow-*.svg` 流程图，以及既有 `ai-draft-flow.svg`，共 23 幅图。

截图通过真实账号登录隔离服务生成。人员、账号、任务、例行模板与报表均为脚本建立的虚构资料，橙色编号和示例标记是截图时临时添加的说明层。没有读取现有业务数据库、真实飞书资料或凭据，不写入演练站和生产库。飞书对话仅使用流程示意，不制作虚假客户端截图。

更新界面后重新生成：

```bash
python3 scripts/build-manual-flows.py
WMT_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright python3 scripts/capture-manual.py
```

需预先安装 Playwright 与 Chromium。截屏脚本创建临时 SQLite 数据库，以 `WMT_AUTH_MODE=account`、`WMT_MONTHLY_DEMO=0` 启动隔离服务；结束时停止服务并删除临时数据。生成流程图不需要浏览器。

生成后核对截图上的编号与手册文字，使用 `tests/user-manual.browser.cjs` 验证图片加载、图号顺序、大图缩放、手机阅读和打印。该脚本要求显式指定本机隔离站点 `WMT_MANUAL_TEST_URL` 及对应测试账号，不能指向生产站点。
