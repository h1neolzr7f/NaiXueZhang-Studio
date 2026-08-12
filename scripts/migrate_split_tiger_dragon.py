"""一次性拆分绝凶虎 / 破坏龙为两个独立本地账号。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ACCOUNTS_PATH = DATA / "pixiv_accounts.local.json"
STATS_PATH = DATA / "pixiv_account_stats.json"
MARKER = DATA / ".accounts_split_tiger_dragon"
TIGER_ID = "acc_tiger794715"
DRAGON_ID = "acc_d156fdcd8f"
TIGER_UID = 126794715
DRAGON_UID = 126812080


def _int(v) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def main() -> None:
    if MARKER.exists():
        print("已拆分过，跳过")
        return
    if not ACCOUNTS_PATH.exists():
        print("无账号配置，跳过")
        return

    raw = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    accounts = list(raw.get("accounts") or [])
    if len(accounts) != 1:
        print(f"账号数={len(accounts)}，非预期单账号，跳过自动拆分")
        return

    old = accounts[0]
    label = str(old.get("label") or "")
    user_name = str(old.get("user_name") or "")
    mixed = ("绝凶虎" in label and "破坏龙" in user_name) or (
        "破坏龙" in label and "绝凶虎" in user_name
    )
    if not mixed and _int(old.get("pixiv_user_id")) not in (TIGER_UID, DRAGON_UID):
        print("未检测到绝凶虎/破坏龙混绑，跳过")
        return

    now = datetime.now().isoformat(timespec="seconds")
    persona = old.get("persona") if isinstance(old.get("persona"), dict) else {}
    direction = str(old.get("direction") or "").strip()
    token = str(old.get("refresh_token") or "").strip()
    created = old.get("created_at") or now

    dragon = {
        "id": DRAGON_ID,
        "label": "破坏龙",
        "refresh_token": token,
        "pixiv_user_id": str(DRAGON_UID),
        "user_name": "理塘の破坏龙",
        "user_account": "user_esxd2572",
        "direction": "AI 生成图爱好者，分享 NovelAI 同人插画",
        "persona": {},
        "created_at": created,
        "updated_at": now,
    }
    tiger = {
        "id": TIGER_ID,
        "label": "绝凶虎",
        "refresh_token": "",
        "pixiv_user_id": str(TIGER_UID),
        "user_name": "理塘の绝凶虎",
        "user_account": "user_puej7572",
        "direction": direction or "AI 生成图爱好者，分享 NovelAI 同人插画",
        "persona": persona,
        "created_at": now,
        "updated_at": now,
    }

    raw["accounts"] = [dragon, tiger]
    raw["active_id"] = DRAGON_ID if token else TIGER_ID
    ACCOUNTS_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("已拆分账号：破坏龙 + 绝凶虎（绝凶虎需重新通行密钥登录）")

    if STATS_PATH.exists():
        try:
            stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            stats = {"accounts": {}}
        histories = stats.setdefault("accounts", {})
        old_hist = list(histories.get(DRAGON_ID) or [])
        histories[TIGER_ID] = [
            h for h in old_hist if _int(h.get("pixiv_user_id")) == TIGER_UID
        ]
        histories[DRAGON_ID] = [
            h for h in old_hist if _int(h.get("pixiv_user_id")) == DRAGON_UID
        ]
        STATS_PATH.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("已按 Pixiv uid 拆分统计数据")

    legacy = DATA / "pixiv_chrome_profile"
    tiger_prof = DATA / "pixiv_chrome_profiles" / TIGER_ID
    dragon_prof = DATA / "pixiv_chrome_profiles" / DRAGON_ID
    if legacy.exists() and not tiger_prof.exists():
        shutil.copytree(legacy, tiger_prof, dirs_exist_ok=True)
        print(f"已将旧浏览器配置复制到绝凶虎：{tiger_prof}")
    dragon_prof.mkdir(parents=True, exist_ok=True)

    MARKER.write_text(now, encoding="utf-8")
    print("完成")


if __name__ == "__main__":
    main()