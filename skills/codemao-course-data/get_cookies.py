#!/usr/bin/env python3
"""
CRM Cookie 全自动获取工具

用法: python get_cookies.py

工作流程：
1. 启动无头浏览器打开 CRM 登录页
2. 检测是否已登录（查找 auth Cookie）
3. 未登录则等待最多 60 秒（期间人工登录）
4. 检测到登录态后自动保存所有 CRM Cookie

依赖: pip install playwright && playwright install chromium
"""
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = str(Path(__file__).parent.resolve())

AUTH_COOKIE_NAMES = [
    "internal_account_token",
    "admin-authorization",
    "session_id",
    "session_token",
    "access_token",
    "Authorization",
]


def load_config():
    cfg = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(cfg):
        with open(cfg, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def has_auth_cookie(context):
    """检查浏览器 context 中是否包含认证 Cookie"""
    cookies = context.cookies()
    for c in cookies:
        if c["name"] in AUTH_COOKIE_NAMES:
            return True
    return False


def save_cookies(context, cookies_file):
    """从 context 提取所有 Cookie 并保存"""
    all_cookies = context.cookies()

    cookie_dict = {}
    for c in all_cookies:
        cookie_dict[c["name"]] = c["value"]

    os.makedirs(os.path.dirname(os.path.abspath(cookies_file)), exist_ok=True)
    with open(cookies_file, "w", encoding="utf-8") as f:
        json.dump(cookie_dict, f, ensure_ascii=False, indent=2)

    return cookie_dict


def get_chromium_path():
    """尝试找到 Chromium 可执行文件路径"""
    # 常见位置
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Chromium\chrome.exe"),
        os.path.expandvars(r"%APPDATA%\..\Local\Chromium\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None  # 让 Playwright 自动找


def main():
    # 动态 import，避免没有安装时报错
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误: 未安装 Playwright")
        print("请运行以下命令安装：")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    config = load_config()
    cookies_file = config.get(
        "cookies_file",
        os.path.join(BASE_DIR, "crm_cookies.json")
    )

    print("=== CRM Cookie 全自动获取 ===")
    print(f"目标: {cookies_file}\n")

    url = "https://codecamp-crm.codemao.cn"

    with sync_playwright() as p:
        # 优先用系统 Chromium，找不到则让 Playwright 自动下载
        chromium_path = get_chromium_path()
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-service-initialize",
            "--disable-default-apps",
        ]

        try:
            if chromium_path:
                print(f"使用系统 Chromium: {chromium_path}")
                browser = p.chromium.launch(
                    executable_path=chromium_path,
                    headless=True,
                    args=browser_args,
                )
            else:
                browser = p.chromium.launch(
                    headless=True,
                    args=browser_args,
                )
        except Exception as e:
            print(f"启动失败 ({e})，尝试无头模式...")
            browser = p.chromium.launch(headless=True)

        context = browser.contexts[0] if browser.contexts else browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        page = context.new_page()

        # 拦截 network 请求，便于调试（可注释掉）
        # page.on("request", lambda r: None)

        print(f"打开: {url}")
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
        except Exception:
            print("页面加载超时，继续等待...")

        print("\n检查登录状态...")
        if has_auth_cookie(context):
            print("✓ 已检测到登录态，获取 Cookie...")
        else:
            print("未检测到登录态，等待人工登录（最多 60 秒）...")
            print("请在浏览器窗口中完成登录...")
            start = time.time()
            deadline = start + 60

            while time.time() < deadline:
                time.sleep(2)
                if has_auth_cookie(context):
                    elapsed = int(time.time() - start)
                    print(f"✓ 检测到登录态（耗时 {elapsed}s），获取 Cookie...")
                    break
                remaining = int(deadline - time.time())
                if remaining % 10 == 0 and remaining > 0:
                    print(f"  仍在等待... ({remaining}s remaining)")
            else:
                print("超时（60s），未检测到登录态")

        # 无论如何都尝试保存（可能已有旧 Cookie）
        try:
            cookie_dict = save_cookies(context, cookies_file)
        except Exception as e:
            print(f"获取 Cookie 失败: {e}")
            browser.close()
            sys.exit(1)

        browser.close()

    if not cookie_dict:
        print("未能获取到任何 Cookie")
        sys.exit(1)

    print(f"\n已保存 {len(cookie_dict)} 个 Cookie → {cookies_file}")

    # 验证关键 Cookie
    auth_keys = [k for k in cookie_dict if k in AUTH_COOKIE_NAMES]
    if auth_keys:
        print(f"✓ 认证 Cookie: {auth_keys}")
        print("Cookie 获取成功！")
    else:
        print("⚠ 未检测到认证 Cookie，Cookie 可能不完整")
        print(f"  实际 Cookie: {list(cookie_dict.keys())}")
        print("  请确认已在浏览器中完整登录（不只是扫码）")


if __name__ == "__main__":
    main()
