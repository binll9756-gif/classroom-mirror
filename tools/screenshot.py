# -*- coding: utf-8 -*-
"""给试讲教室界面截图（用本机 Edge，不需要下载 Chromium）

跑法：先起服务，再
    python tools/screenshot.py http://127.0.0.1:8012 截图.png
"""
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8012"
OUT = sys.argv[2] if len(sys.argv) > 2 else "shot.png"


def launch(p):
    for ch in ("msedge", "chrome"):
        try:
            b = p.chromium.launch(channel=ch, headless=True)
            print(f"用浏览器通道：{ch}")
            return b
        except Exception as e:
            print(f"  通道 {ch} 不可用：{type(e).__name__}")
    try:
        b = p.chromium.launch(headless=True)
        print("用内置 chromium")
        return b
    except Exception as e:
        print("❌ 没有可用浏览器：", e)
        return None


def main() -> int:
    with sync_playwright() as p:
        browser = launch(p)
        if browser is None:
            return 1
        pg = browser.new_page(viewport={"width": 1500, "height": 940})
        pg.goto(URL, wait_until="load")
        pg.wait_for_timeout(1500)
        print("页面标题：", pg.title())

        # ① 生成学生
        pg.click("#btnCast")
        pg.wait_for_timeout(2500)
        print("学生卡片数：", pg.locator(".stu").count())

        def send(text: str, wait_ms: int = 6000):
            pg.fill("#say", text)
            pg.click("#btnSay")
            pg.wait_for_timeout(wait_ms)

        def yield_to_student(hold_ms: int = 3200):
            if pg.locator("#pending").is_visible():
                pg.wait_for_timeout(hold_ms)      # 演示"等待 3 秒"
                pg.click("#btnYield")
                pg.wait_for_timeout(7000)

        send("今天我们来学有理数的乘法。(-3)×(-2) 等于多少？小林你来说说。")
        yield_to_student(3300)
        send("那你能说说你是怎么想的吗？")
        yield_to_student(900)

        pg.wait_for_timeout(1200)
        pg.screenshot(path=OUT)
        print("已截图：", OUT)

        # ② 报告页（生成要 10~20 秒，必须等元素真的出现，不能靠死等）
        pg.click("#btnFinish")
        try:
            pg.wait_for_selector("#report", state="visible", timeout=90000)
            pg.wait_for_timeout(2500)
        except Exception as e:
            print("  报告未出现：", type(e).__name__)
        rep = OUT.replace(".png", "_report.png")
        pg.screenshot(path=rep)
        print("已截图（报告）：", rep)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
