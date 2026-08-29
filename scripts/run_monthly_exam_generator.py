#!/usr/bin/env python3
"""Run 月考反馈助手.exe to generate wrong-question reports and awards.

The exe is a PyInstaller console app with a menu:
  [1] 自动模式  [2] 手动模式  [3] 设置奖状线  [q] 退出

It prints emoji progress and crashes on GBK output when stdout is redirected,
so we spawn it inside a ConPTY pseudo terminal (pywinpty) and forward output.

Menu automation:
  - if --award-threshold given, first choose [3], type the threshold, confirm;
  - then choose [1] auto mode to (re)generate reports and awards;
  - when generation finishes (menu returns or "物料补齐"), send [q] to exit.
"""
import argparse
import re
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", default=r"C:\Users\PC\Desktop\月考反馈助手\月考反馈助手\月考反馈助手.exe")
    parser.add_argument("--award-threshold", type=int, default=0,
                        help="奖状线分数；>0 时自动进入 [3] 设置（如 80），然后 [1] 自动模式重新生成")
    parser.add_argument("--auto-only", action="store_true",
                        help="不设置奖状线，直接进入 [1] 自动模式")
    parser.add_argument("--idle-exit-seconds", type=int, default=45)
    args = parser.parse_args()

    exe = Path(args.exe)
    if not exe.is_file():
        raise RuntimeError(f"未找到月考反馈助手程序：{exe}")

    try:
        from winpty import PtyProcess
    except ImportError as error:
        raise RuntimeError("缺少 pywinpty：请执行 py -3.10 -m pip install pywinpty") from error

    print(f"启动月考反馈助手：{exe}", flush=True)
    proc = PtyProcess.spawn(str(exe))
    last_output = time.monotonic()
    sent_enter = False
    threshold_set = args.award_threshold <= 0
    auto_started = False
    exited = False

    def send(text: str, note: str = "") -> None:
        nonlocal last_output, sent_enter
        try:
            proc.write(text)
            if note:
                print(f"\n[发送] {note}", flush=True)
            last_output = time.monotonic()
            sent_enter = False
        except Exception as error:
            print(f"\n[发送失败] {error}", flush=True)

    buffer = ""
    try:
        while True:
            try:
                chunk = proc.read(4096)
            except EOFError:
                print("\n[月考反馈助手进程已结束]", flush=True)
                break
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                buffer = (buffer + chunk)[-4000:]
                last_output = time.monotonic()
                sent_enter = False

                # 菜单出现 -> 只做物料补齐，绝不进入 [1] 自动模式（那是企业微信发送流程）
                if "请选择" in buffer or "请选择:" in chunk:
                    if exited:
                        send("q\r\n", "退出")
                        time.sleep(1.5)
                        break
                    if not threshold_set:
                        send("3\r\n", "进入[3]设置奖状线")
                        threshold_set = True
                        time.sleep(1.2)
                        send(f"{args.award_threshold}\r\n", f"奖状线设为 {args.award_threshold}")
                        time.sleep(1.0)
                        buffer = ""
                        continue
                    # 奖状线已设置（或无需设置）：物料补齐由 exe 启动/设置时自动完成，直接退出
                    exited = True
                    send("q\r\n", "物料补齐完成，退出")
                    time.sleep(1.5)
                    continue
                buffer = ""
            else:
                if proc.isalive() is False:
                    print("\n[月考反馈助手进程已结束]", flush=True)
                    break
                now = time.monotonic()
                if now - last_output > args.idle_exit_seconds and not sent_enter:
                    try:
                        proc.write("\r\n")
                        sent_enter = True
                        print("\n[长时间无输出，发送回车]", flush=True)
                    except Exception:
                        pass
                time.sleep(0.4)
    finally:
        if proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:
                pass
    print("月考反馈物料生成流程结束。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
