"""通过浏览器登录 Pixiv，自动获取 refresh_token 并写入本机账号配置。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="浏览器登录 Pixiv 并保存 refresh_token")
    parser.add_argument("-u", "--username", default="", help="Pixiv 邮箱/ID")
    parser.add_argument("-p", "--password", default="", help="Pixiv 密码")
    args = parser.parse_args()

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from pixiv_accounts import get_active_account_id, login_with_email_password
    from pixiv_browser_login import proxy_settings

    print("=" * 56)
    print("Pixiv 浏览器登录获取 refresh_token")
    print("=" * 56)
    proxy = proxy_settings()
    if proxy:
        print(f"使用系统代理: {proxy['server']}")
    print()

    username = str(args.username or "").strip()
    password = str(args.password or "")
    if not username or not password:
        print("未提供账号密码，将仅打开浏览器等待手动登录…")
        from pixiv_browser_login import browser_login_pixiv_sync
        from pixiv_accounts import update_account_token, add_account, test_account_auth

        result = browser_login_pixiv_sync()
        token = str(result.get("refresh_token") or "").strip()
        active_id = get_active_account_id()
        if active_id:
            out = update_account_token(active_id, token)
        else:
            out = add_account(refresh_token=token)
        auth = out.get("auth") or test_account_auth(active_id)
        print("[成功]" if auth.get("ok") else "[失败]", auth.get("message"))
        return 0 if auth.get("ok") else 1

    try:
        result = login_with_email_password(
            username,
            password,
            account_id=get_active_account_id() or None,
        )
    except Exception as exc:
        print(f"登录失败: {exc}")
        return 1

    if result.get("ok"):
        print(f"[成功] {result.get('message')}")
        return 0
    print(f"[失败] {result.get('message')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())