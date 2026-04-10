#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BigQuery 客户端配置（共用模块）

使用方法:
    from bq_client import get_bq_client, setup_proxy
    
    setup_proxy()  # 设置代理
    client = get_bq_client()  # 获取客户端
"""

import os
import subprocess
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

PROJECT_ID = "diyl-407103"


def setup_proxy():
    """设置代理环境变量"""
    proxy_url = "http://127.0.0.1:7897"
    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url


def get_gcloud_credentials():
    """使用 gcloud 的 access token 创建 Credentials"""
    token_result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True
    )
    access_token = token_result.stdout.strip()
    
    return Credentials(
        token=access_token,
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )


def get_bq_client(project_id: str = PROJECT_ID):
    """获取 BigQuery 客户端"""
    credentials = get_gcloud_credentials()
    return bigquery.Client(project=project_id, credentials=credentials)


# 门店 UUID 列表（53家）
STORE_UUIDS = [
    "1958987436032000", "2269470793728000", "2598648160256000", "2876210421760000",
    "3446618988544000", "3870122057728000", "4149605310464000", "4358842359808000",
    "4912616316928000", "5567347171328000", "5999171739648000", "6542950670336000",
    "6789240201216000", "6977459593216000", "7191251656704000", "7400123801600000",
    "7648065888256000", "7863653113856000", "8100551598080000", "8501761941504000",
    "8722592047104000", "2947521978368000", "3448951017472000", "3782477877248000",
    "4024875094016000", "4229506797568000", "4418766376960000", "4613872816128000",
    "4805464432640000", "5001267122176000", "5250979205120000", "5444022046720000",
    "7600687026176000", "7813128523776000", "8051063001088000", "8535580610560000",
    "8723170856960000", "1515821506560000", "3631470387200000", "5498438983680000",
    "9231705128960000", "1379607252992000", "1745354756096000", "1919875551232000",
    "2277263806464000", "2618629820416000", "2788834676736000", "2992442970112000",
    "3169446793216000", "3367514411008000", "3662462062592000", "4197894328320000",
    "3087884357632000",
]
