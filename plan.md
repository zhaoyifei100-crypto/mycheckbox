# MyCheckBox 多站点签到方案

## 最终结论

MyCheckBox 不需要运行 MyCheckBox 二进制。每个站点只需要：

1. 读取该站点的加密 Cookie 文件；
2. 使用项目独立密钥在运行时解密；
3. 向 `/index.php?action=addbonus` 发一次带 Cookie 的 HTTPS GET；
4. 根据返回页面判断签到成功、今日已签到、Cookie 失效或访问被拦截。

运行链路沿用现有股票任务：

```text
Cloud Scheduler → Cloud Run Job → Python requests → 多个 PT 网站
                              ↘ Secret Manager 读取项目独立解密密钥
                              ↘ GitHub Raw 读取加密 Cookie JSON
```

当前 GCloud 环境：项目 `project-048627af-e7d4-4972-9d3`，区域 `us-west1`，
现有股票任务使用 Cloud Run Job `daily-monitor`。MyCheckBox 使用独立 Job、独立服务账号
和独立 Secret，不复用股票任务的 Cookie，也不复用 Telegram Token。Telegram 消息发送
暂不实现。

## 站点目录

站点由项目根目录的 `sites.json` 统一管理：

```json
[
  {
    "name": "ptschool",
    "url": "https://pt.btschool.club",
    "encrypted_cookie_url": "https://raw.githubusercontent.com/.../cookies/ptschool.cookie.enc"
  },
  {
    "name": "keepfrds",
    "url": "https://pt.keepfrds.com",
    "encrypted_cookie_url": "https://raw.githubusercontent.com/.../cookies/keepfrds.cookie.enc"
  }
]
```

新增站点只需增加一个目录项、一个本地明文 Cookie 文件，并重新生成对应的密文文件。
程序会逐站点执行；某站点失败不会阻止其他站点，但只要有站点失败，Job 最终返回非零
退出码，便于 Cloud Run/Scheduler 发现问题。

当前站点：

- `ptschool`：`https://pt.btschool.club`
- `keepfrds`：`https://pt.keepfrds.com`

## Cookie 加密保存

### 密钥

已经在 Secret Manager 创建独立 Secret：

```text
Secret ID: mycheckbox-cookie-key
```

它保存一个随机生成的 32 字节项目密钥，当前已有 version 1。密钥本地备份位置为：

```text
/Users/zhao/Projects/mycheckbox/.secrets/mycheckbox-cookie-key
```

该文件权限为 `600`，并被 `.gitignore` 和 `.gcloudignore` 排除。密钥值不写入代码、
普通环境变量、镜像、日志或 GitHub。

### 密文

本地明文 Cookie 只放在：

```text
/Users/zhao/Projects/mycheckbox/.secrets/ptschool-cookie.txt
/Users/zhao/Projects/mycheckbox/.secrets/keepfrds-cookie.txt
```

本地工具 `tools/encrypt_cookies.py` 使用项目密钥派生站点隔离的 AES-256-GCM 密钥，
为每个文件随机生成 salt 和 nonce，生成：

```text
/Users/zhao/Projects/mycheckbox/cookies/ptschool.cookie.enc
/Users/zhao/Projects/mycheckbox/cookies/keepfrds.cookie.enc
```

密文文件可以提交到 GitHub。程序每次运行从 `encrypted_cookie_url` 读取最新 JSON，
再从 Secret Manager 读取 `mycheckbox-cookie-key` 解密。程序不会把明文 Cookie 写回磁盘，
也不会输出 Cookie、请求头或完整网页。

GitHub Raw URL 当前不带认证，因此存放密文的仓库必须允许匿名读取；即使仓库公开，
没有 Secret Manager 中的项目密钥也无法解密 Cookie。若以后改为私有仓库，需要另行设计
GitHub 认证方式，不能把 GitHub Token 写入 `sites.json`。

### Cookie 更新

Cookie 失效时：

1. 将新的 Cookie 粘贴到对应 `.secrets/*-cookie.txt`；
2. 运行 `source .venv/bin/activate && python tools/encrypt_cookies.py`；
3. 只提交对应的 `cookies/*.cookie.enc`；
4. 推送到 GitHub；下一次 Cloud Run Job 会读取新密文。

项目密钥没有变化，因此不需要重新配置 Cloud Run。若项目密钥泄露，需要重新生成密钥、
重新加密所有站点 Cookie、创建新的 Secret version，并确认旧 version 不再可访问。

## 请求与结果判断

- 只允许站点 URL 为 HTTPS。
- 请求不自动跟随重定向，避免 Cookie 被带到其他域名。
- 识别签到成功、今日已签到、登录失效、HTTP 错误、Cloudflare 拦截和未知页面。
- 登录有效时提取账户摘要：用户名、魔力值、邀请、分享率、H&R、上传量、下载量、
  做种数、下载中数量、可连接状态和连接数。
- 不自动重试，减少触发站点风控的可能。
- 单站点运行结果和异常只输出站点名、状态和脱敏统计。

## 本地运行

```bash
cd /Users/zhao/Projects/mycheckbox
source .venv/bin/activate
pip install -r requirements.txt
MYCHECKBOX_COOKIE_KEY_FILE=.secrets/mycheckbox-cookie-key \
MYCHECKBOX_ENCRYPTED_COOKIE_DIR=cookies \
python -m src.checkin
```

本地测试显式使用 `.secrets/mycheckbox-cookie-key` 和 `cookies/`，不接触 Secret Manager。
部署到 Cloud Run 时不设置这两个本地变量，程序自动从 Secret Manager 和 GitHub Raw
读取。这样本地测试不会被 gcloud 的认证状态阻塞。

## Cloud Run 部署

先把代码、`sites.json` 和 `cookies/*.cookie.enc` 推送到 GitHub，然后在本地执行：

```bash
cd /Users/zhao/Projects/mycheckbox
make setup-iam
make build
make scheduler
```

`setup-iam` 会创建独立运行时服务账号，并只授予它读取
`mycheckbox-cookie-key` 的 `roles/secretmanager.secretAccessor` 权限。Docker 镜像只复制
`src/` 和非敏感的 `sites.json`，不复制 `.secrets/`、`tools/` 或明文 Cookie。

## 验收标准

- [x] 本地 venv 已创建。
- [x] PTSchool 和 KeepFrDS 的 Cookie 已成功访问站点并解析账户摘要。
- [x] 独立 Secret `mycheckbox-cookie-key` 已创建，version 1 已写入。
- [x] 两个站点已生成 AES-GCM 加密 Cookie 文件。
- [ ] 将代码和 `cookies/*.cookie.enc` 推送到 GitHub。
- [ ] 构建并部署独立 Cloud Run Job。
- [ ] 手动执行成功，确认两个站点均返回正常结果。
- [ ] Scheduler 手动触发成功后观察一周访问记录。
