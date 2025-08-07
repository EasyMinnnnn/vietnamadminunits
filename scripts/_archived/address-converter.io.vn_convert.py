import pandas as pd
import requests
from tqdm import tqdm
import time
import sqlite3
import json
from tenacity import *
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

headers = {
    'origin': 'https://address-converter.io.vn',
    'referer': 'https://address-converter.io.vn/',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
}
url = 'https://address-converter.io.vn/api/convert-address'

@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def scrap_convert(payload):

    try:
        res = requests.post(url, json=payload, headers=headers)
        json_data = res.json()
        data = json_data['data']
        return data
    except Exception as e:
        error_log = {
            'payload': payload,
            'json': json_data,
            'exception': e
        }
        print(error_log)

        retry_after = json_data.get('retryAfter', 0)
        if retry_after:
            print(f'Waiting {retry_after} seconds...')
            time.sleep(retry_after)
            raise Exception('Retry after limit')
        return json_data


if __name__ == '__main__':
    df_ward = pd.read_csv(BASE_DIR / 'data/address-converter.io.vn_ward.csv',
                          dtype={'wardCode': str, 'districtCode': str, 'provinceCode': str})
    df_ward.fillna('', inplace=True)

    data_new_wards = []
    error_logs = []

    # df_ward = df_ward.loc[2846:,]
    for _, row in tqdm(df_ward.iterrows(), total=len(df_ward)):
        payload = {
            'detailAddress': '',
            'direction': 'old-to-new',
            'districtCode': row['districtCode'],
            'provinceCode': row['provinceCode'],
            'wardCode': row['wardCode'],
        }

        try:
            data = scrap_convert(payload)
            dump_payload = json.dumps(payload)
            dump_data = json.dumps(data)
            data_new_wards.append({'payload': dump_payload, 'data': dump_data})
        except Exception as e:
            print(e)

        time.sleep(60 / 13)

        if ((_ + 1) % 15 == 0) or ((_ + 1)==len(df_ward)):
            df_new_wards = pd.DataFrame(data_new_wards)
            with sqlite3.connect(BASE_DIR / 'data/address-converter.io.vn_convert.db') as conn:
                df_new_wards.to_sql(name='convert', con=conn, if_exists='append', index=False)
            data_new_wards = []