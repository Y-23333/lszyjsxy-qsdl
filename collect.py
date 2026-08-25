#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电费数据采集脚本（用于 GitHub Actions 自动累积数据）
- 从环境变量读取 BUILD_ID, ROOM_ID, DINGTALK_TOKEN
- 采集成功后追加写入 data.jsonl（该文件会提交到仓库，实现历史累积）
- 日志输出到 stdout，不记录敏感信息
- 低电量时通过钉钉告警，每日最多一次
"""

import os
import json
import logging
import sys
import requests
from datetime import datetime

# ==================== 强制环境变量检查 ====================
required_env_vars = ["BUILD_ID", "ROOM_ID"]
missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
if missing_vars:
    raise RuntimeError(f"缺少必需的环境变量: {', '.join(missing_vars)}，请在 GitHub Secrets 中设置。")

BUILD_ID = os.environ["BUILD_ID"]
ROOM_ID = os.environ["ROOM_ID"]
DINGTALK_TOKEN = os.environ.get("DINGTALK_TOKEN", "")

API_URL = f"http://zhxy.lszjy.com/api/pay/lszy/query/bill?buildId={BUILD_ID}&roomId={ROOM_ID}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                  "MicroMessenger/7.0.17(0x17001126) NetType/WIFI Language/zh_CN"
}

DATA_FILE = "data.jsonl"
ALERT_THRESHOLD = 20
ALERT_FLAG_FILE = "last_alert_date.txt"

if DINGTALK_TOKEN:
    DINGTALK_URL = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_TOKEN}"
else:
    DINGTALK_URL = None

# ==================== 日志初始化（stdout） ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout
)


def fetch_data():
    """请求接口并解析数据，返回 (used, all_amp) 元组；失败返回 None。"""
    try:
        resp = requests.get(API_URL, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            logging.warning(f"接口返回非 200 状态码: {resp.status_code}")
            return None
        data = resp.json()
        if isinstance(data, list):
            if len(data) == 0:
                logging.warning("返回列表为空")
                return None
            record = data[0]
        elif isinstance(data, dict):
            record = data
        else:
            logging.error(f"未知的数据类型: {type(data)}")
            return None
        used = float(record["usedAmp"])
        all_amp = float(record["allAmp"])
        return used, all_amp
    except Exception:
        # 不记录详细异常，避免泄露 URL 参数
        logging.error("获取数据失败，请检查网络或接口状态")
        return None


def append_to_jsonl(used, all_amp):
    """将一条记录原子化追加到 data.jsonl。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {"time": now_str, "used": used, "all": all_amp}
    line = json.dumps(record, ensure_ascii=False) + "\n"

    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            f.write(line)
        return

    tmp_file = DATA_FILE + ".tmp"
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f_old:
            old_content = f_old.read()
        with open(tmp_file, "w", encoding="utf-8") as f_tmp:
            f_tmp.write(old_content)
            f_tmp.write(line)
        os.replace(tmp_file, DATA_FILE)
    except Exception:
        logging.error("写入数据文件失败")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def send_dingtalk_alert(remaining):
    """发送钉钉告警，返回是否成功。"""
    if not DINGTALK_URL:
        return False
    try:
        message = {
            "msgtype": "text",
            "text": {
                "content": f"【电量预警】房间 {BUILD_ID}-{ROOM_ID} 剩余电量不足！\n当前剩余：{remaining:.2f} 度\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n请及时充值。"
            }
        }
        resp = requests.post(DINGTALK_URL, json=message, timeout=10)
        return resp.status_code == 200
    except Exception:
        logging.error("发送钉钉消息失败")
        return False


def should_alert():
    """判断今天是否已经告警过（通过标记文件）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if not os.path.exists(ALERT_FLAG_FILE):
        return True
    with open(ALERT_FLAG_FILE, "r") as f:
        last_date = f.read().strip()
    return last_date != today


def set_alert_flag():
    """写入今天的日期作为告警标记。"""
    today = datetime.now().strftime("%Y-%m-%d")
    with open(ALERT_FLAG_FILE, "w") as f:
        f.write(today)


def main():
    result = fetch_data()
    if result is None:
        logging.warning("未能获取有效数据，本次跳过。")
        return

    used, all_amp = result
    remaining = all_amp - used

    # 输出采集结果（安全）
    logging.info(f"采集成功: used={used}, all={all_amp}, 剩余={remaining:.2f}")

    # 追加数据到 data.jsonl
    append_to_jsonl(used, all_amp)

    # 低电量告警
    if remaining <= ALERT_THRESHOLD and should_alert():
        if send_dingtalk_alert(remaining):
            set_alert_flag()
            logging.info("钉钉告警已发送。")
        else:
            logging.error("钉钉告警发送失败。")


if __name__ == "__main__":
    main()
