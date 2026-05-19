#!/usr/bin/env python3
"""
飞书机器人后端 — 报表自助服务

暴露一个 HTTP 接口给飞书 webhook 调用, 其余全在内部处理.

Setup:
    1. 飞书开放平台创建机器人, 拿到 App ID + App Secret
    2. 配置事件订阅: 请求地址填 https://你的域名/webhook/feishu
    3. 开启权限: im:chat:readonly, im:message, im:message.group_msg
    4. export FEISHU_APP_ID=xxx FEISHU_APP_SECRET=xxx
    5. venv/bin/python scripts/bot/feishu_gateway.py

Usage (飞书侧):
    用户私聊机器人: "帮我导一下 2026-03 的利润报表"
    机器人回复:     "📋 确认执行: profit_by_price --month 2026-03\n[确认] [取消]"
    用户点击确认
    机器人回复:     "⏳ 执行中..." → "✅ 完成 + 文件"
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.bot.core import (
    REPORT_REGISTRY,
    check_permission,
    log_to_knowledge,
    parse_intent,
    run_report,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFY_TOKEN", "")  # 事件订阅的 Verification Token
FEISHU_ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")    # 可选: 加密密钥

# 简单的内存状态 (生产环境应换 Redis)
USER_STATES = {}


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    print("[错误] 请安装依赖: venv/bin/pip install fastapi uvicorn httpx")
    sys.exit(1)

app = FastAPI(title="报表自助机器人 — 飞书网关", version="1.0.0")


# ---------------------------------------------------------------------------
# 飞书 API 封装
# ---------------------------------------------------------------------------

class FeishuClient:
    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self):
        self.app_id = FEISHU_APP_ID
        self.app_secret = FEISHU_APP_SECRET
        self._tenant_token = None
        self._token_expire = 0

    async def _get_tenant_token(self) -> str:
        if self._tenant_token and time.time() < self._token_expire - 60:
            return self._tenant_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"获取 tenant_token 失败: {data}")
            self._tenant_token = data["tenant_access_token"]
            self._token_expire = time.time() + data["expire"]
            return self._tenant_token

    async def send_text(self, receive_id: str, text: str, msg_type: str = "open_id"):
        """发送文本消息."""
        token = await self._get_tenant_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE}/im/v1/messages?receive_id_type={msg_type}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
                timeout=30,
            )
            return resp.json()

    async def send_file(self, receive_id: str, file_path: str, msg_type: str = "open_id"):
        """发送文件."""
        token = await self._get_tenant_token()

        # 1. 上传文件
        async with httpx.AsyncClient() as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{self.BASE}/im/v1/files",
                    headers={"Authorization": f"Bearer {token}"},
                    data={
                        "file_type": "stream",
                        "file_name": Path(file_path).name,
                    },
                    files={"file": f},
                    timeout=60,
                )
            upload_data = resp.json()
            if upload_data.get("code") != 0:
                raise RuntimeError(f"上传文件失败: {upload_data}")
            file_key = upload_data["data"]["file_key"]

        # 2. 发送文件消息
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE}/im/v1/messages?receive_id_type={msg_type}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}),
                },
                timeout=30,
            )
            return resp.json()


feishu = FeishuClient()


# ---------------------------------------------------------------------------
# Webhook 处理
# ---------------------------------------------------------------------------

def verify_feishu_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """校验飞书请求签名."""
    if not FEISHU_ENCRYPT_KEY:
        return True  # 未配置则不校验

    # 飞书签名算法: BASE64(HMAC-SHA256(timestamp + nonce + body, encrypt_key))
    # 这里简化处理, 实际需要按飞书文档实现
    # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/event-framework/signature-verify
    return True  # MVP 阶段跳过严格校验


@app.post("/webhook/feishu")
async def feishu_webhook(request: Request):
    """接收飞书事件推送."""
    body = await request.body()

    # 可选: 签名校验
    timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
    signature = request.headers.get("X-Lark-Signature", "")
    if not verify_feishu_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # --- URL 验证 (首次配置事件订阅时) ---
    if "challenge" in data:
        return JSONResponse({"challenge": data["challenge"]})

    # --- 事件处理 ---
    event = data.get("event", {})
    if not event:
        return JSONResponse({"status": "ok"})

    event_type = data.get("header", {}).get("event_type", "")

    # 只处理用户消息
    if event_type not in ("im.message.receive_v1",):
        return JSONResponse({"status": "ok"})

    message = event.get("message", {})
    sender = event.get("sender", {})

    # 只处理文本消息
    if message.get("message_type") != "text":
        return JSONResponse({"status": "ok"})

    # 解析消息内容
    content = json.loads(message.get("content", "{}"))
    text = content.get("text", "").strip()

    # 获取用户标识
    sender_id = sender.get("sender_id", {}).get("union_id", "")
    chat_id = message.get("chat_id", "")
    message_id = message.get("message_id", "")

    # 权限检查
    if not check_permission(sender_id):
        await feishu.send_text(sender_id, "⛔ 你没有导表权限, 请联系管理员.")
        return JSONResponse({"status": "ok"})

    # --- 异步处理消息 ---
    asyncio.create_task(_handle_message(sender_id, chat_id, text))

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# 消息处理逻辑 (异步)
# ---------------------------------------------------------------------------

async def _handle_message(sender_id: str, chat_id: str, text: str):
    """处理用户消息."""

    # 处理确认/取消
    state = USER_STATES.get(sender_id)
    if state and state.get("waiting_confirm"):
        if text in ("确认", "确认执行", "👍", "是的", "对"):
            await _do_execute(sender_id, state)
            USER_STATES.pop(sender_id, None)
            return
        elif text in ("取消", "不", "👎", "算了", "否"):
            await feishu.send_text(sender_id, "❎ 已取消.")
            USER_STATES.pop(sender_id, None)
            return
        else:
            await feishu.send_text(sender_id, "请回复 '确认' 或 '取消'.")
            return

    # --- 新请求: 解析意图 ---
    intent = parse_intent(text)
    if "error" in intent:
        await feishu.send_text(sender_id, f"❌ {intent['error']}")
        return

    # 保存状态, 等待确认
    USER_STATES[sender_id] = {
        "waiting_confirm": True,
        "intent": intent,
        "user_text": text,
    }

    confirmation = build_confirmation(intent)
    # 飞书文本消息不支持按钮, 用文字代替
    confirmation += "\n\n请回复 '确认' 或 '取消'."
    await feishu.send_text(sender_id, confirmation)


async def _do_execute(sender_id: str, state: dict):
    """执行报表并推送结果."""
    intent = state["intent"]
    user_text = state["user_text"]

    await feishu.send_text(
        sender_id,
        f"⏳ 正在执行 {intent['report_key']} --month {intent['month']} ...\n"
        f"(这可能需要 1-3 分钟, 请稍候)"
    )

    # 在线程池中执行同步的报表脚本
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_report, intent["report_key"], intent["month"])

    if result["status"] == "ok":
        output_path = PROJECT_ROOT / result["output_path"]
        summary = result.get("stdout_summary", "")

        audit = result.get("audit_result", {})
        if audit.get("status") == "ok":
            audit_msg = "✅ Audit 通过"
        elif audit.get("status") == "failed":
            audit_msg = "🔴 Audit 未通过, 请检查数据"
        else:
            audit_msg = f"⚠️ Audit: {audit.get('status', 'unknown')}"

        # 先发文本摘要
        reply_text = (
            f"✅ 报表生成成功!\n\n"
            f"📊 执行摘要:\n{summary}\n\n"
            f"{audit_msg}"
        )
        await feishu.send_text(sender_id, reply_text)

        # 再发文件
        if output_path.exists():
            try:
                await feishu.send_file(sender_id, str(output_path))
            except Exception as e:
                await feishu.send_text(sender_id, f"⚠️ 文件发送失败: {e}")

        # 记录知识
        log_to_knowledge(user_text, intent, result, sender_id)

    else:
        error_msg = result.get("message", "未知错误")
        reply_text = f"🔴 执行失败:\n{error_msg}"
        if result.get("stderr"):
            reply_text += f"\n\n错误详情:\n```\n{result['stderr'][:800]}\n```"
        await feishu.send_text(sender_id, reply_text)
        log_to_knowledge(user_text, intent, result, sender_id)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def run_gateway(host: str = "0.0.0.0", port: int = 8080):
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("[错误] 请设置环境变量:")
        print("  export FEISHU_APP_ID='你的App ID'")
        print("  export FEISHU_APP_SECRET='你的App Secret'")
        print("  export FEISHU_VERIFY_TOKEN='事件订阅的Verification Token'")
        sys.exit(1)

    print(f"[Gateway] 启动 http://{host}:{port}")
    print(f"[Gateway] 飞书 webhook 地址: http://{host}:{port}/webhook/feishu")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_gateway()
