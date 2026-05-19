# 飞书机器人接入指南

> 后端已写好 (`scripts/bot/feishu_gateway.py`)，丢给前端/运维同事接飞书即可。

## 后端提供什么

一个 HTTP 服务，暴露两个接口：

```
POST /webhook/feishu   ← 飞书事件推送（用户发消息）
GET  /health           ← 健康检查
```

## 飞书侧要配什么

### 1. 创建机器人

飞书开放平台 → 创建企业自建应用 → 添加机器人

拿到：
- App ID
- App Secret
- Verification Token（事件订阅验证用）

### 2. 开启权限

应用能力 → 权限管理 → 开启：
- `im:chat:readonly`
- `im:message`
- `im:message.group_msg`

### 3. 配置事件订阅

事件与回调 → 请求地址：
```
https://你的域名/webhook/feishu
```

添加事件：`im.message.receive_v1`

### 4. 发布版本

版本管理与发布 → 创建版本 → 申请发布

## 部署后端

```bash
# 1. 配置环境变量
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_VERIFY_TOKEN="xxx"

# 2. 启动
venv/bin/python scripts/bot/feishu_gateway.py

# 或 systemd
sudo cp scripts/bot/report-bot.service /etc/systemd/system/
# 修改 ExecStart 指向 feishu_gateway.py
sudo systemctl start report-bot
```

## 交互流程

```
用户(飞书): 帮我导一下 2026-03 的利润报表
机器人:      📋 确认执行: profit_by_price --month 2026-03
            执行后会自动跑 audit...
            请回复 '确认' 或 '取消'.

用户:        确认
机器人:      ⏳ 正在执行 profit_by_price --month 2026-03 ...
            (这可能需要 1-3 分钟)

机器人:      ✅ 报表生成成功!
            📊 执行摘要: ...
            ✅ Audit 通过
            [文件]
```

## 安全机制

1. **签名校验** — 飞书 webhook 带签名, 后端校验（需配 `FEISHU_ENCRYPT_KEY`）
2. **权限白名单** — `core.py` 的 `ALLOWED_USERS` 控制谁可以导表
3. **二次确认** — 执行前必须回复"确认"
4. **自动 audit** — 不通过不交付
5. **超时控制** — 脚本最多执行 10 分钟

## 扩展

### 加新报表

在 `core.py` 的 `REPORT_REGISTRY` 中加一行, 重启服务即可。

### 改飞书交互

飞书消息不支持 Markdown 按钮, 纯文本交互。
如需卡片/按钮, 需改 `feishu_gateway.py` 的 `send_text` 为 `send_card`,
参考飞书开放平台文档。

### 群聊支持

当前只处理私聊 (`chat_type: p2p`).
如需群聊, 改 `_handle_message` 中的 `chat_id` 逻辑。
