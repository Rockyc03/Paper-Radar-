# -*- coding: utf-8 -*-
"""企业微信智能机器人绑定 / 测试小工具（可选功能，不绑定也能正常出日报）。

常用：
    .venv\\Scripts\\python.exe wecom_bind.py --bot-id XXX --secret YYY   # 写入凭证并开始绑定
    .venv\\Scripts\\python.exe wecom_bind.py                            # 已有凭证，直接绑定
    .venv\\Scripts\\python.exe wecom_bind.py --test                     # 发一条测试消息
    .venv\\Scripts\\python.exe wecom_bind.py --status                   # 看当前状态
    .venv\\Scripts\\python.exe wecom_bind.py --enable / --disable        # 开关自动推送
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radar import wecom  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="企业微信智能机器人绑定工具")
    ap.add_argument("--bot-id", default=None, help="智能机器人 Bot ID")
    ap.add_argument("--secret", default=None, help="智能机器人 Secret")
    ap.add_argument("--chatid", default=None, help="已有 chatid 可直接填，跳过抓取")
    ap.add_argument("--chat-type", default=None, type=int, choices=[1, 2], help="1 单聊 2 群聊")
    ap.add_argument("--test", action="store_true", help="只发一条测试消息")
    ap.add_argument("--timeout", type=int, default=180, help="绑定时等待用户发消息的秒数")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--enable", action="store_true", help="打开自动推送")
    ap.add_argument("--disable", action="store_true", help="关闭自动推送")
    args = ap.parse_args()

    if args.bot_id or args.secret:
        wecom.save_conf({"bot_id": args.bot_id or "", "secret": args.secret or ""})
        print("已写入凭证。")

    if args.chatid:
        wecom.save_conf({"chatid": args.chatid, "chat_type": args.chat_type or 2})
        print("已写入 chatid：%s" % args.chatid)

    if args.enable:
        wecom.save_conf({"enabled": True})
        print("自动推送已打开。")
    if args.disable:
        wecom.save_conf({"enabled": False})
        print("自动推送已关闭。")

    if args.status:
        st = wecom.status()
        print("开关：%s" % ("开" if st["enabled"] else "关"))
        print("凭证：%s" % (st["bot_id_masked"] or "未填"))
        print("会话：%s（类型 %s，%s）" % (st["chat_label"] or "-", st["chat_type"], "已绑定" if st["bound"] else "未绑定"))
        print("可发送：%s %s" % ("是" if st["ready"] else "否", st["reason"]))
        for lg in st["logs"][-5:]:
            print("  %s %s %s" % (lg.get("ts"), lg.get("event"), lg.get("error") or ""))
        return

    if args.test:
        r = wecom.test()
        print("发送结果：", r)
        return

    conf = wecom.load_conf()
    if not conf.get("bot_id") or not conf.get("secret"):
        print("还没有 Bot ID / Secret。")
        print("企业微信管理后台 → 智能机器人 → 你的机器人 → API 模式 → 长连接，")
        print("把页面上的 Bot ID 和 Secret 填进来：")
        print("  python wecom_bind.py --bot-id 你的BotID --secret 你的Secret")
        return

    print("正在连接 wss://openws.work.weixin.qq.com …")
    print("订阅成功后，请在**机器人所在的群**里 @ 它随便说一句话（或直接私聊它发一条）。")
    print("最多等 180 秒，收到就自动绑定并回一条确认消息。\n")
    try:
        got = wecom.bind(timeout=args.timeout)
    except Exception as e:  # noqa: BLE001
        print("绑定失败：%s" % e)
        return
    print("绑定成功：chatid=%s 类型=%s 名称=%s" % (got["chatid"], got["chat_type"],
                                                 got.get("chat_label") or "-"))
    wecom.save_conf({"enabled": True})
    print("自动推送已打开。发一条测试消息：")
    print(" ", wecom.test())


if __name__ == "__main__":
    main()
