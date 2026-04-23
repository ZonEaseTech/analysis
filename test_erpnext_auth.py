#!/usr/bin/env python3
"""
调试 ERPNext 认证和价格查询的独立脚本。

用法:
    1. 复制 .env.example 为 .env，填入你的认证信息
    2. 运行: ./venv/bin/python3 test_erpnext_auth.py
"""

import base64
import os
import sys

import requests
from dotenv import load_dotenv

# 加载 .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BASE_URL = os.environ.get("ERPNEXT_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("ERPNEXT_API_KEY", "").strip()
API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "").strip()
USERNAME = os.environ.get("ERPNEXT_USERNAME", "").strip()
PASSWORD = os.environ.get("ERPNEXT_PASSWORD", "").strip()
PRICE_LIST = os.environ.get("ERPNEXT_PRICE_LIST", "Standard Buying").strip()

# AES 密钥（GoFrame gaes 的默认值）
AES_KEY = "IesahquufojahCaiceet7Pha".encode("utf-8")


def decrypt_gaes(encrypted_base64):
    """
    尝试多种 gaes 解密方式。
    返回 (decrypted_secret, method) 或 (None, None)
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    try:
        encrypted = base64.b64decode(encrypted_base64)
    except Exception:
        return None, None

    results = []

    # 方法 1: 密文包含 IV（前 16 字节）
    if len(encrypted) >= 32:
        iv = encrypted[:16]
        ciphertext = encrypted[16:]
        cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
        try:
            plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
            results.append((plaintext.decode("utf-8"), "IV_in_ciphertext"))
        except Exception:
            pass

    # 方法 2: IV = key[:16]
    iv = AES_KEY[:16]
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
    try:
        plaintext = unpad(cipher.decrypt(encrypted), AES.block_size)
        results.append((plaintext.decode("utf-8"), "IV_from_key"))
    except Exception:
        pass

    # 方法 3: 无 padding 直接解密（IV from key）
    plaintext = cipher.decrypt(encrypted)
    try:
        # 检查末尾是否有有效 padding
        pad_byte = plaintext[-1]
        if pad_byte <= 16 and all(b == pad_byte for b in plaintext[-pad_byte:]):
            results.append((plaintext[:-pad_byte].decode("utf-8"), "manual_unpad"))
    except Exception:
        pass

    # 方法 4: ECB 模式
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    try:
        plaintext = unpad(cipher.decrypt(encrypted), AES.block_size)
        results.append((plaintext.decode("utf-8"), "ECB_mode"))
    except Exception:
        pass

    if results:
        return results[0]  # 返回第一个成功的
    return None, None


def test_api_key_auth():
    """测试 API Key 认证。"""
    if not API_KEY or not API_SECRET:
        print("❌ ERPNEXT_API_KEY 或 ERPNEXT_API_SECRET 未设置")
        return False

    print(f"\n📌 测试 API Key 认证（HTTP Basic Auth）")
    
    # 尝试解密
    # decrypted, method = decrypt_gaes(API_SECRET)
    decrypted = API_SECRET
    secret_to_use = decrypted if decrypted else API_SECRET

    # 构建 Basic Auth header
    credentials = base64.b64encode(f"{API_KEY}:{secret_to_use}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}
    
    url = f"{BASE_URL}/api/method/frappe.auth.get_logged_user"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        print(f"   Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"   ✅ API Key 认证成功!")
            return True
        else:
            print(f"   ❌ 认证失败: {resp.text[:100]}")
    except Exception as e:
        print(f"   ❌ 请求异常: {e}")

    return False


def test_password_auth():
    """测试用户名密码认证。"""
    if not USERNAME or not PASSWORD:
        print("❌ ERPNEXT_USERNAME 或 ERPNEXT_PASSWORD 未设置")
        return False

    print(f"\n📌 测试用户名密码认证")
    print(f"   URL: {BASE_URL}")
    print(f"   Username: {USERNAME}")

    # Frappe 密码认证：先获取 CSRF token / 登录
    session = requests.Session()

    # 尝试直接 Basic Auth（新版 Frappe 支持）
    url = f"{BASE_URL}/api/method/frappe.auth.get_logged_user"
    try:
        resp = session.get(url, auth=(USERNAME, PASSWORD), timeout=30)
        print(f"   Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"   ✅ 密码认证成功!")
            return True
        else:
            print(f"   ❌ 认证失败: {resp.text[:100]}")
    except Exception as e:
        print(f"   ❌ 请求异常: {e}")

    return False


def test_item_price():
    """测试获取 Item Price。"""
    print(f"\n📌 测试 Item Price 查询")
    print(f"   Price List: {PRICE_LIST}")

    # 先用 API Key 试
    if API_KEY and API_SECRET:
        decrypted, _ = decrypt_gaes(API_SECRET)
        secret = decrypted or API_SECRET
        auth_headers = {"Authorization": f"token {API_KEY}:{secret}"}
    else:
        auth_headers = None

    # 或者直接试密码
    auth = (USERNAME, PASSWORD) if USERNAME and PASSWORD else None

    url = f"{BASE_URL}/api/resource/Item Price"
    params = {
        "fields": '["item_code","price_list_rate"]',
        "filters": f'[["price_list","=","{PRICE_LIST}"]]',
        "limit_page_length": 5,
    }

    try:
        resp = requests.get(
            url,
            params=params,
            headers=auth_headers,
            auth=auth,
            timeout=30,
        )
        print(f"   Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            print(f"   ✅ 成功获取 {len(data)} 条记录")
            for item in data:
                print(f"      {item.get('item_code')}: {item.get('price_list_rate')}")
            return True
        else:
            print(f"   ❌ 查询失败: {resp.text[:200]}")
    except Exception as e:
        print(f"   ❌ 请求异常: {e}")

    return False


def main():
    print("=" * 50)
    print("ERPNext 认证 & 价格查询调试")
    print("=" * 50)

    if not BASE_URL:
        print("❌ ERPNEXT_BASE_URL 未设置")
        sys.exit(1)

    # 1. API Key 测试
    api_ok = test_api_key_auth()

    # 2. 密码认证测试
    pwd_ok = test_password_auth()

    # 3. Item Price 测试（任意一种认证成功都能跑）
    if api_ok or pwd_ok:
        test_item_price()
    else:
        print("\n⚠️  两种认证都失败了，无法获取 Item Price")
        print("\n建议:")
        print("  1. 检查 ERPNEXT_BASE_URL 是否正确")
        print("  2. 在 ERPNext 后台重新生成 API Key")
        print("  3. 确认用户名密码正确")
        print("  4. 检查是否有 IP 白名单限制")

    print("\n" + "=" * 50)


if __name__ == "__main__":
    main()
