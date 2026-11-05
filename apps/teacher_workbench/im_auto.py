# -*- coding: utf-8 -*-
"""IM 自动回复引擎。

孩子一发消息：
  ① 秒回一句（配置里的 instant_text，1~2 秒内发出）
  ② 后台抓他最新已解锁课程的作答与代码 → 豆包分析出卡点 → 追答一条针对性解答

轮询走 scripts/poll_im_new_messages.mjs（约 1 秒），发送走 CRM 正规接口。
同一条学生消息只处理一次，按 sendTime 记账。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import im_ai

WORKSPACE = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE / "data"
STATE_PATH = DATA_DIR / "im-auto-state.json"
MAX_LOG = 200

STUDENT_FROM = 1
HELP_FROM = 4


def _now_ms() -> int:
    return int(time.time() * 1000)


def _today_start_ms() -> int:
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)


def _clock(ms: int | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime((ms or _now_ms()) / 1000))


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"handled": {}, "log": []}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trimmed = {
        "handled": state.get("handled") or {},
        "log": (state.get("log") or [])[-MAX_LOG:],
    }
    STATE_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


class AutoReplyEngine:
    """常驻后台线程：轮询 → 秒回 → 抓题分析 → 追答。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        stored = _load_state()
        self._handled: dict[str, dict[str, Any]] = stored.get("handled") or {}
        self._log: list[dict[str, Any]] = stored.get("log") or []
        self._running = False
        self._last_tick_at = 0
        self._last_error = ""
        self._tick_count = 0

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return {"ok": True, "message": "自动回复已在运行"}
            self._stop.clear()
            self._running = True
            self._last_error = ""
            self._thread = threading.Thread(target=self._loop, daemon=True, name="im-auto-reply")
            self._thread.start()
            self._add_log("engine", "自动回复已启动")
        return {"ok": True, "message": "自动回复已启动"}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return {"ok": True, "message": "自动回复未在运行"}
            self._stop.set()
            self._running = False
            self._add_log("engine", "自动回复已停止")
        _save_state({"handled": self._handled, "log": self._log})
        return {"ok": True, "message": "自动回复已停止"}

    def status(self) -> dict[str, Any]:
        settings = im_ai.im_ai_settings()
        with self._lock:
            return {
                "running": self._running,
                "lastTickAt": self._last_tick_at,
                "lastError": self._last_error,
                "tickCount": self._tick_count,
                "handledCount": len(self._handled),
                "log": list(reversed(self._log[-60:])),
                "settings": {
                    "instant_text": settings["instant_text"],
                    "poll_seconds": settings["poll_seconds"],
                    "deep_reply": settings["deep_reply"],
                    "direct_answer": settings["direct_answer"],
                },
            }

    def mark_handled(self, user_id: int, send_time: int, name: str = "", note: str = "") -> None:
        self._handled[str(user_id)] = {
            "lastSendTime": int(send_time),
            "name": name,
            "at": _now_ms(),
            "note": note,
        }
        _save_state({"handled": self._handled, "log": self._log})

    # ------------------------------------------------------------------ 内部
    def _add_log(self, stage: str, text: str, **extra: Any) -> None:
        entry = {"at": _now_ms(), "clock": _clock(), "stage": stage, "text": text}
        entry.update(extra)
        self._log.append(entry)
        if len(self._log) > MAX_LOG:
            self._log = self._log[-MAX_LOG:]

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = im_ai.im_ai_settings()["poll_seconds"]
            try:
                self._tick()
            except Exception as error:  # noqa: BLE001 - 引擎不能因为单次异常退出
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                    self._add_log("error", f"本轮异常：{error}")
            self._stop.wait(interval)

    def _tick(self) -> None:
        with self._lock:
            self._tick_count += 1
            self._last_tick_at = _now_ms()

        result = im_ai.poll_candidates()
        if not result.get("ok"):
            with self._lock:
                self._last_error = str(result.get("error") or "轮询失败")
                self._add_log("error", "轮询失败：" + self._last_error)
            return
        with self._lock:
            self._last_error = ""

        day_start = _today_start_ms()
        for candidate in result.get("candidates") or []:
            if self._stop.is_set():
                return
            self._handle_candidate(candidate, day_start)

    def _handle_candidate(self, candidate: dict[str, Any], day_start: int) -> None:
        user_id = int(candidate.get("userId") or 0)
        term_id = int(candidate.get("termId") or 0)
        class_id = int(candidate.get("classId") or 0)
        name = candidate.get("name") or "同学"
        messages = candidate.get("messages") or []
        if not user_id or not term_id or not messages:
            return

        last = messages[-1]
        if last.get("from") not in (STUDENT_FROM, HELP_FROM):
            return  # 老师是最后一个说话的
        send_time = int(last.get("sendTime") or 0)
        if send_time < day_start:
            return  # 不是今天发的
        done = self._handled.get(str(user_id)) or {}
        if int(done.get("lastSendTime") or 0) >= send_time:
            return  # 这条已经回过了

        question = im_ai.message_text(last)
        self._add_log("detect", f"{name} 发来消息：{question[:60]}", userId=user_id, name=name)

        # ① 秒回
        settings = im_ai.im_ai_settings()
        instant = str(settings["instant_text"]).replace("{name}", name)
        sent = im_ai.send_im_message(user_id, term_id, instant)
        if not sent.get("ok"):
            self._add_log("error", f"{name} 秒回失败：{sent.get('error')}", userId=user_id, name=name)
            return
        self._add_log("instant", instant, userId=user_id, name=name)
        # 先记账，避免秒回成功后追答阶段出错导致重复秒回
        self.mark_handled(user_id, send_time, name, note="秒回已发")

        if not settings["deep_reply"]:
            return

        # 纯应答（好的 / ok / 谢谢 这类）只秒回，不追答；其余一律追答
        if im_ai.is_pure_acknowledgement(last):
            self._add_log(
                "skip",
                f"{name} 回的是「{question[:20]}」，只秒回不追答",
                userId=user_id,
                name=name,
            )
            return

        # ② 抓最新代码题
        payload = im_ai.fetch_student_questions(user_id, term_id, class_id)
        questions = im_ai.wrong_questions(payload) if payload.get("ok") else []
        course_name = payload.get("courseName") or ""

        # ③ 豆包分析
        task: dict[str, Any] = {
            "userId": user_id,
            "name": name,
            "courseName": course_name,
            "lastActiveText": _clock(send_time),
            "signals": [
                {
                    "text": question,
                    "kind": im_ai.message_kind(last),
                    "sendTime": send_time,
                    "strong": last.get("from") == HELP_FROM,
                }
            ],
            "transcript": [
                {"who": "老师" if m.get("from") == 2 else "学生", "kind": im_ai.message_kind(m), "text": im_ai.message_text(m)[:200], "t": m.get("sendTime")}
                for m in messages[-10:]
            ],
            "questions": questions,
        }
        im_ai.analyze_task(task)
        ai = task.get("ai") or {}
        if not ai.get("ok"):
            self._add_log(
                "error",
                f"{name} 分析失败：{ai.get('error') or '未知'}（已只发秒回）",
                userId=user_id,
                name=name,
            )
            return

        # ④ 追答
        reply = str(ai.get("reply") or "").strip()
        if not reply:
            self._add_log("error", f"{name} 分析结果为空，未追答", userId=user_id, name=name)
            return
        sent2 = im_ai.send_im_message(user_id, term_id, reply)
        if not sent2.get("ok"):
            self._add_log("error", f"{name} 追答失败：{sent2.get('error')}", userId=user_id, name=name)
            return
        self._add_log(
            "deep",
            reply,
            userId=user_id,
            name=name,
            summary=ai.get("summary") or "",
            category=ai.get("category") or "",
            courseName=course_name,
        )


ENGINE = AutoReplyEngine()
