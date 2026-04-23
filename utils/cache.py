#!/usr/bin/env python3
"""
文件缓存层 —— 避免重复查询 BQ / ERPNext / 外部资源。

按 key = hash(数据源 + 参数) 存取 JSON 文件，支持 TTL。
缓存目录: .cache/bq_reports/
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

# 默认缓存目录（项目根目录下的 .cache）
DEFAULT_CACHE_DIR = Path(".cache") / "bq_reports"


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def cache_key(source: str, params: dict) -> str:
    """生成缓存键：source + 参数哈希（前12位）"""
    payload = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
    return f"{source}_{h}"


def get_cache(
    key: str,
    ttl_seconds: int = 3600,
    cache_dir: Optional[Path] = None,
) -> Optional[Any]:
    """
    读缓存。文件不存在或过期返回 None。

    Args:
        key: 缓存键（由 cache_key 生成）
        ttl_seconds: 缓存有效期，默认 1 小时
        cache_dir: 自定义缓存目录
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > ttl_seconds:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def set_cache(
    key: str,
    data: Any,
    cache_dir: Optional[Path] = None,
) -> None:
    """
    写缓存。

    Args:
        key: 缓存键
        data: 任意可 JSON 序列化的数据
        cache_dir: 自定义缓存目录
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    _ensure_dir(cache_dir)
    path = cache_dir / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, ensure_ascii=False, indent=2)


def cached(
    source: str,
    ttl_seconds: int = 3600,
    cache_dir: Optional[Path] = None,
):
    """
    装饰器：自动缓存函数结果。

    Usage:
        @cached("erpnext_prices", ttl_seconds=3600)
        def load_erpnext_prices(price_list="Standard Buying"):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            params = {"args": args, "kwargs": kwargs}
            key = cache_key(source, params)
            cached_data = get_cache(key, ttl_seconds=ttl_seconds, cache_dir=cache_dir)
            if cached_data is not None:
                print(f"[Cache] 命中: {source}")
                return cached_data
            result = func(*args, **kwargs)
            set_cache(key, result, cache_dir=cache_dir)
            print(f"[Cache] 写入: {source}")
            return result
        return wrapper
    return decorator


def clear_cache(cache_dir: Optional[Path] = None) -> int:
    """清空缓存目录，返回删除文件数。"""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    if not cache_dir.exists():
        return 0
    count = 0
    for f in cache_dir.glob("*.json"):
        f.unlink()
        count += 1
    return count
