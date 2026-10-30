# GitHub Actions 定时邮件

此目录包含一个通过 SMTP 发送浪漫日常邮件的 Python 程序。工作流文件位于 [`.github/workflows/scheduled-email.yml`](../.github/workflows/scheduled-email.yml)，默认在 **Asia/Shanghai 时区每天 08:35** 运行；也可以从 GitHub Actions 页面手动执行。

## 1. 编辑邮件内容

邮件程序会根据星期自动选择 [`templates/`](templates) 下对应的 HTML 页面；七天的色彩和动效各不相同。邮件发送时会自动填入：

- 2025 年中秋节（公历 **2025-10-06**）结婚至今的累计天数；
- 武汉当天的天气、体感温度、最高/最低温与出行提醒；
- 农历日期；农历八月十五会特别提示“生日 + 结婚纪念日”。

如需改写文案或页面风格，编辑对应的 `templates/monday.html` 至 `templates/sunday.html`。不要更改其中的 `{{...}}` 占位符。婚期常量位于 [`send_email.py`](send_email.py) 的 `WEDDING_DATE`。

## 2. 配置 GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 中新增下列 Secrets：

| Secret | 说明 | 示例 |
| --- | --- | --- |
| `SMTP_HOST` | SMTP 服务器地址 | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP 端口 | `587` |
| `SMTP_USERNAME` | SMTP 登录用户名 | `sender@example.com` |
| `SMTP_PASSWORD` | SMTP 密码或应用专用密码 | 不要使用普通密码 |
| `MAIL_FROM` | 发件人地址 | `sender@example.com` |
| `MAIL_TO` | 收件人，多个地址以逗号、分号或换行分隔 | `a@example.com,b@example.com` |
| `MAIL_SUBJECT` | 邮件主题 | `每日提醒` |
| `SMTP_USE_SSL` | SSL 直连（通常为 465 端口） | `false` |
| `SMTP_STARTTLS` | STARTTLS（通常为 587 端口） | `true` |

`SMTP_USE_SSL` 和 `SMTP_STARTTLS` 必须只有一个为 `true`。Gmail、QQ 邮箱等通常需要创建**应用专用密码**，不要把任何实际凭据写入代码、HTML 或提交记录。

## 3. 调整发送时间

编辑工作流中的 `on.schedule`：

```yaml
schedule:
  - cron: "35 8 * * *"
    timezone: "Asia/Shanghai"
```

这是 POSIX cron 格式，按顺序为“分钟 小时 日期 月份 星期”。GitHub Actions 只会使用默认分支最新提交中的工作流。若要先测试配置，请在 **Actions → Scheduled Email → Run workflow** 勾选 `dry_run`，它会渲染当天内容但不会发送邮件。

## 本地验证（可选）

Python 3.12+：

```bash
cd scheduled-email
python3 -m pip install -r requirements.txt
set -a && source .env && set +a
python3 send_email.py --dry-run
```

确认无误后移除 `--dry-run` 才会发送邮件。
