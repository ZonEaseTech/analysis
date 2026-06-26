#!/usr/bin/env python3
import requests, os
from dotenv import load_dotenv
load_dotenv('.env')

BASE = os.environ['ERPNEXT_BASE_URL'].rstrip('/')
SID = os.environ['ERPNEXT_SID']

codes = ['CK01001', 'BE05001', 'PA01001', 'DR02001', 'SE01001']
resp = requests.get(
    f'{BASE}/api/resource/Item',
    params={
        'fields': '["item_code","stock_uom","uoms"]',
        'filters': f'[["item_code","in",{codes}]]',
        'limit_page_length': len(codes)
    },
    cookies={'sid': SID},
    timeout=30
)
print(f'Status: {resp.status_code}')
for item in resp.json().get('data', []):
    print(f"\n{item['item_code']}:")
    print(f"  stock_uom: {item.get('stock_uom')}")
    for u in item.get('uoms', []):
        print(f"  {u['uom']}: factor={u['conversion_factor']}")
