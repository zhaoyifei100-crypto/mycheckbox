# MyCheckBox

多站点 PT 签到 Cloud Run Job。核心逻辑在
[`src/checkin.py`](/Users/zhao/Projects/mycheckbox/src/checkin.py)：读取 GitHub 上的加密
Cookie，运行时从 Google Secret Manager 读取项目独立密钥解密，然后向每个站点发送一次
带 Cookie 的 HTTP GET。

站点目录在 [`sites.json`](/Users/zhao/Projects/mycheckbox/sites.json)。当前包含：

- PTSchool：`https://pt.btschool.club`
- KeepFrDS：`https://pt.keepfrds.com`

本地明文 Cookie 和独立密钥只保存在 `.secrets/`，不会提交到 GitHub；可提交的只有
`cookies/*.cookie.enc`。加密工具 [`tools/encrypt_cookies.py`](/Users/zhao/Projects/mycheckbox/tools/encrypt_cookies.py)
是本地工具，已被 Git 和 Cloud Build 排除。

本地测试：

```bash
cd /Users/zhao/Projects/mycheckbox
source .venv/bin/activate
pip install -r requirements.txt
MYCHECKBOX_COOKIE_KEY_FILE=.secrets/mycheckbox-cookie-key \
MYCHECKBOX_ENCRYPTED_COOKIE_DIR=cookies \
python -m src.checkin
```

详细设计、密钥轮换和 Cloud Run 部署步骤见 [`plan.md`](/Users/zhao/Projects/mycheckbox/plan.md)。
Telegram 消息发送暂未实现。

Cloud Run 日志会自动进入 Cloud Logging；项目另外配置了 `mycheckbox-logs` 专用 bucket，
保留 30 天，只收集 `mycheckbox` Job 的日志。查询日志：

```bash
make logs
```

输出包含北京时间、Cloud Run execution ID 和日志内容，并按最新执行倒序排列。

Cloud Scheduler `mycheckbox` 已配置为每天北京时间 19:00 执行 Cloud Run Job；周日运行时
会额外发送本周日志邮件。

每周日 Job 还会从专用 Cloud Logging bucket 读取本周一至今天的日志，直接写入邮件正文，
不发送附件。发件邮箱、收件邮箱和 QQ 授权码保存在本地 JSON 配置中，加密后通过 GitHub
Raw 读取；明文 JSON 不会进入 Git 或 Docker 镜像。
