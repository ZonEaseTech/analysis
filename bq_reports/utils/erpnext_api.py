#!/usr/bin/env python3
"""
通过 ERPNext (Frappe) REST API 获取 Item Price，供利润报表使用。

对 ERPNext 只读一次，零写入。价格加载到内存 dict，在 Python 聚合阶段替换成本。

.env 文件放在项目根目录：
    ERPNEXT_BASE_URL=https://your-erpnext.com
    ERPNEXT_API_KEY=your_api_key
    ERPNEXT_API_SECRET=encrypted_or_plain_secret

支持密文（AES-192-CBC，PKCS7，Base64）自动解密。
"""

import base64
import os
from typing import Dict, Optional, List

import requests

# 对齐 ttpos main/app/service/business_cost_profit_erp_cost.go:103 priceList='Buying - Internal'
# 对齐 ttpos-bmp .../stock/item.go:107 GetItemUnitCost
COST_PROFIT_PRICE_LIST = "Buying - Internal"
ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT = False

# AES 解密配置（与 ttpos-bmp 一致）
_AES_KEY = "IesahquufojahCaiceet7Pha".encode("utf-8")  # 24 字节 = AES-192


def _decrypt_api_secret(encrypted_base64: str) -> str:
    """
    解密 AES-192-CBC 加密的 ApiSecret。
    若解密失败则原样返回（可能已是明文）。
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    try:
        encrypted_data = base64.b64decode(encrypted_base64)
        iv = encrypted_data[:16]
        ciphertext = encrypted_data[16:]
        cipher = AES.new(_AES_KEY, AES.MODE_CBC, iv)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return plaintext.decode("utf-8")
    except Exception:
        # 解密失败，假设已是明文
        return encrypted_base64


def _get_auth(sid: Optional[str] = None):
    """从环境变量读取认证信息，自动解密密文。"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

    base_url = os.environ.get("ERPNEXT_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("请设置环境变量 ERPNEXT_BASE_URL")

    # 优先使用传入的 sid 或 .env 中的 sid
    env_sid = os.environ.get("ERPNEXT_SID", "").strip()
    if sid or env_sid:
        return base_url, (sid or env_sid)

    api_key = os.environ.get("ERPNEXT_API_KEY", "").strip()
    api_secret_encrypted = os.environ.get("ERPNEXT_API_SECRET", "").strip()
    username = os.environ.get("ERPNEXT_USERNAME", "").strip()
    password = os.environ.get("ERPNEXT_PASSWORD", "").strip()

    if api_key and api_secret_encrypted:
        api_secret = _decrypt_api_secret(api_secret_encrypted)
        return base_url, (api_key, api_secret)
    elif username and password:
        return base_url, (username, password)
    else:
        raise RuntimeError(
            "ERPNext 认证信息不完整。请在 .env 中设置:\n"
            "  ERPNEXT_SID（推荐，从浏览器 Cookie 复制）\n"
            "  或 ERPNEXT_API_KEY + ERPNEXT_API_SECRET\n"
            "  或 ERPNEXT_USERNAME + ERPNEXT_PASSWORD"
        )


def _api_get(base_url: str, auth, doctype: str, fields: List[str], filters: Optional[List] = None, limit: int = 0):
    """调用 Frappe API 获取数据。支持 token / basic auth / cookie sid。"""
    url = f"{base_url}/api/resource/{doctype}"
    params = {
        "fields": str(fields).replace("'", '"'),
        "limit_page_length": limit,
    }
    if filters:
        params["filters"] = str(filters).replace("'", '"')

    headers = {}
    cookies = {}

    # auth 可能是 (api_key, api_secret) / (username, password) / sid 字符串
    if isinstance(auth, str):
        # Session Cookie 方式：最靠谱
        cookies["sid"] = auth
    elif isinstance(auth, tuple) and len(auth) == 2:
        # 优先 Basic Auth（Frappe 也支持）
        import base64 as b64
        creds = b64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    resp = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def load_erpnext_item_last_purchase(
    item_codes: Optional[List[str]] = None,
    sid: Optional[str] = None,
) -> Dict[str, tuple[float, str]]:
    """
    从 ERPNext `Item` 主数据取 last_purchase_rate（最近采购单价），返回
    {item_code: (rate, stock_uom)}。

    用途：当账号无 `Item Price` doctype 读权限（403），但能读 `Item` 时，
    用 Item 自带的 last_purchase_rate 作成本源。口径 = 最近一次采购入库单价
    （非加权均价；wallace-th 实例 valuation_rate 全 0，last_purchase_rate 才是
    唯一有真实值的成本字段）。

    Args:
        item_codes: 可选，只加载指定物料编码（IN 过滤，分批）
        sid: 可选，Frappe session ID

    Returns:
        item_code -> (last_purchase_rate, stock_uom) 的字典
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    base_url, auth = _get_auth(sid=sid)

    fields = ["item_code", "last_purchase_rate", "stock_uom"]
    rows: List[dict] = []
    if item_codes:
        # frappe 的 IN 过滤 URL 不能太长，分批查
        B = 90
        for i in range(0, len(item_codes), B):
            chunk = item_codes[i:i + B]
            rows += _api_get(
                base_url, auth, doctype="Item", fields=fields,
                filters=[["item_code", "in", chunk]], limit=0,
            )
    else:
        rows = _api_get(base_url, auth, doctype="Item", fields=fields, limit=0)

    prices: Dict[str, tuple[float, str]] = {}
    for row in rows:
        code = row.get("item_code")
        if not code:
            continue
        prices[code] = (
            float(row.get("last_purchase_rate") or 0),
            row.get("stock_uom", "") or "",
        )
    nonzero = sum(1 for v in prices.values() if v[0] > 0)
    print(f"[ERPNext API] 加载 {len(prices)} 个 Item 的 last_purchase_rate "
          f"(价>0: {nonzero})")
    return prices


def _pick_item_price_row(rows: List[dict], desired_uom: Optional[str] = None) -> Optional[dict]:
    """
    从同一物料的多条 Item Price 行中选出最佳一行。

    对齐 ttpos preferItemUnitCost:
        ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go:385

    选行策略:
    - 有 desired_uom: 只接受 uom 精确匹配(大小写不敏感)的行,多行取 modified 最新。
                     匹配不到返回 None(数据缺口,显式暴露,不拿错 UOM 的价充数)。
    - 无 desired_uom: 回退现有 UOM_PRIORITY(g>pc>nos>pkt>ctn) + modified 逻辑,
                      兼容旧调用方不传 desired_uoms 时的行为。
    """
    if not rows:
        return None

    UOM_PRIORITY = {"g": 0, "gm": 0, "pc": 1, "nos": 2, "pkt": 3, "ctn": 4}

    if desired_uom:
        target = desired_uom.strip().lower()
        cands = [r for r in rows if (r.get("uom") or "").strip().lower() == target]
        if not cands:
            return None  # 无对应 UOM 价行 = 数据缺口,显式暴露
        return max(cands, key=lambda r: r.get("modified", ""))

    # 无 desired_uom: 沿用 UOM_PRIORITY + modified(兼容旧调用)
    best = None
    best_priority = 99
    best_modified = ""
    for row in rows:
        uom = (row.get("uom") or "").lower()
        p = UOM_PRIORITY.get(uom, 99)
        m = row.get("modified", "")
        if p < best_priority or (p == best_priority and m > best_modified):
            best = row
            best_priority = p
            best_modified = m
    return best


def load_erpnext_prices(
    price_list: Optional[str] = None,
    item_codes: Optional[List[str]] = None,
    sid: Optional[str] = None,
    allow_last_purchase: bool = ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT,
    desired_uoms: Optional[Dict[str, str]] = None,
) -> Dict[str, tuple[float, str]]:
    """
    从 ERPNext 获取 Item Price，返回 {item_code: (price, uom)} 字典。

    对齐 ttpos main/app/service/business_cost_profit_erp_cost.go:103
    priceList='Buying - Internal' / ttpos-bmp .../stock/item.go:107 GetItemUnitCost

    Args:
        price_list: 价格表名称，默认从 .env 的 ERPNEXT_PRICE_LIST 读取，
                    缺省 COST_PROFIT_PRICE_LIST ("Buying - Internal")
        item_codes: 可选，只加载指定物料编码
        sid: 可选，Frappe session ID（cookie 认证，最靠谱）
        allow_last_purchase: 显式允许 last_purchase_rate 降级口径（默认 False）。
                             ERPNEXT_PRICE_SOURCE=last_purchase_rate 时，
                             若此参数为 False 则抛 RuntimeError，不再静默切换。
        desired_uoms: 可选，{item_code: uom} 映射，指定各物料期望的消耗单位。
                      有 desired_uom 时按精确匹配选行（对齐 ttpos preferItemUnitCost）；
                      匹配不到的物料不进结果 dict（显式暴露缺口）。
                      不传时回退 UOM_PRIORITY（g>pc>nos>pkt>ctn），行为与旧版一致。

    Returns:
        item_code -> (price_list_rate, uom) 的字典
    """
    if os.environ.get("ERPNEXT_PRICE_SOURCE", "").strip().lower() == "last_purchase_rate":
        if not allow_last_purchase:
            raise RuntimeError(
                "ERPNEXT_PRICE_SOURCE=last_purchase_rate 会把'最近采购价'冒充 Item Price，"
                "口径偏离 ttpos GetItemUnitCost (business_cost_profit_erp_cost.go:103)。"
                "如确需降级请显式传 allow_last_purchase=True。"
            )
        import warnings
        # warn 供程序化捕获/-W error 升级; print 供 ad-hoc 脚本 stdout 人眼可见
        warnings.warn(
            "[ERPNext API] 口径降级: 用 Item.last_purchase_rate 代替 Item Price"
            "(非 ttpos 成本毛利口径 Buying - Internal)",
            stacklevel=2,
        )
        print("[ERPNext API] ⚠️ 口径降级: 用 Item.last_purchase_rate 代替 Item Price"
              "(非 ttpos 口径)")
        return load_erpnext_item_last_purchase(item_codes=item_codes, sid=sid)

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

    base_url, auth = _get_auth(sid=sid)
    # 对齐 ttpos business_cost_profit_erp_cost.go:103: 默认读 "Buying - Internal"
    price_list = (price_list or os.environ.get("ERPNEXT_PRICE_LIST") or COST_PROFIT_PRICE_LIST).strip()

    # 只按 price_list + docstatus 过滤，不依赖 selling 字段
    filters = [
        ["price_list", "=", price_list],
        ["docstatus", "!=", 2],
    ]

    if item_codes:
        # frappe API 的 IN 过滤器格式
        filters.append(["item_code", "in", item_codes])

    rows = _api_get(
        base_url, auth,
        doctype="Item Price",
        fields=["item_code", "price_list_rate", "uom", "modified"],
        filters=filters,
        limit=0,  # 0 = 全部
    )

    # 按 item_code 分组，每组内通过 _pick_item_price_row 选行
    from collections import defaultdict
    grouped: Dict[str, list] = defaultdict(list)
    for row in rows:
        code = row.get("item_code")
        if code:
            grouped[code].append(row)

    prices: Dict[str, tuple[float, str]] = {}
    for code, group in grouped.items():
        # 有 desired_uoms 时按 BOM 消耗单位精确匹配（对齐 ttpos preferItemUnitCost）；
        # 无 desired_uoms 时回退 UOM_PRIORITY，兼容旧调用方不传此参数的行为。
        uom_hint = desired_uoms.get(code) if desired_uoms else None
        best = _pick_item_price_row(group, desired_uom=uom_hint)
        if best is None:
            # desired_uom 指定但无对应行 → 不进结果，上层感知缺口
            continue
        prices[code] = (
            float(best.get("price_list_rate") or 0),
            best.get("uom", "") or "",
        )

    print(f"[ERPNext API] 加载 {len(prices)} 条 Item Price (price_list={price_list})")
    return prices
