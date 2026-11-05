# -*- coding: utf-8 -*-
"""IM 答疑助手：扫描学员消息 -> 识别提问/求助 -> 豆包生成分析与建议回复。

数据来源全部复用浏览器登录态（CDP 驱动 Chrome），见 scripts/fetch_im_overview.mjs。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = WORKSPACE / "scripts"
DATA_DIR = WORKSPACE / "data"
OVERVIEW_PATH = DATA_DIR / "im-overview.json"
STATE_PATH = DATA_DIR / "im-scan-state.json"
TASKS_PATH = DATA_DIR / "im-ai-tasks.json"
CONFIG_PATH = DATA_DIR / "teacher-workbench-config.json"
DOUBAO_ENV_PATH = WORKSPACE / "config" / "doubao.env"

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# 秒回话术默认值（可在 config 的 im_ai.instant_text 覆盖，{name} 会替换成孩子名字）
# 定位：老师自己已有「老师下班了」那类通知在发，这句只负责衔接——让孩子知道消息被看到、
# 马上有人看题，后面紧跟的 AI 解答才是实质内容。
DEFAULT_INSTANT_TEXT = "凹K，let me  看看"

# 学生消息里出现这些词，视为在提问 / 需要帮助
HELP_KEYWORDS = (
    "老师", "怎么", "为什么", "为啥", "不会", "不懂", "不明白", "报错", "错误",
    "错在哪", "哪里错", "帮我", "帮忙", "看看", "卡住", "卡了", "问题", "请问",
    "教教", "讲讲", "运行不了", "跑不了", "没反应", "对不对", "怎么做", "求助", "救命",
)
HELP_QUESTION_FROM = 4        # 求助卡片
HELP_QUESTION_MSGTYPE = 21    # 举手/求助消息类型
TEACHER_FROM = 2
STUDENT_FROM = 1


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_doubao_env() -> dict[str, str]:
    """按 环境变量 > 工作台配置 > config/doubao.env 的顺序解析豆包配置。"""
    values: dict[str, str] = {}
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        config = {}
    rules = config.get("feedback_rules") if isinstance(config, dict) else {}
    wrong_report = rules.get("wrong_report") if isinstance(rules, dict) else {}
    if isinstance(wrong_report, dict):
        values["api_key"] = str(wrong_report.get("doubao_api_key") or "").strip()
        values["model"] = str(wrong_report.get("doubao_model") or "").strip()
        values["base_url"] = str(wrong_report.get("doubao_base_url") or "").strip()
    if DOUBAO_ENV_PATH.exists():
        try:
            for raw in DOUBAO_ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip().upper()
                value = value.strip().strip('"').strip("'")
                if not value:
                    continue
                if key == "DOUBAO_API_KEY":
                    values.setdefault("api_key", value)
                elif key == "DOUBAO_MODEL":
                    values.setdefault("model", value)
                elif key == "DOUBAO_BASE_URL":
                    values.setdefault("base_url", value)
        except Exception:
            pass
    api_key = os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY") or values.get("api_key", "")
    model = os.environ.get("DOUBAO_MODEL") or os.environ.get("ARK_MODEL") or values.get("model", "")
    base_url = (
        os.environ.get("DOUBAO_BASE_URL")
        or os.environ.get("ARK_BASE_URL")
        or values.get("base_url", "")
        or DEFAULT_BASE_URL
    )
    return {"api_key": api_key.strip(), "model": model.strip(), "base_url": base_url.strip().rstrip("/")}


def im_ai_settings() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        config = {}
    section = config.get("im_ai") if isinstance(config, dict) else {}
    if not isinstance(section, dict):
        section = {}
    return {
        "auto_scan": bool(section.get("auto_scan", False)),
        "auto_reply": bool(section.get("auto_reply", False)),
        "instant_text": str(section.get("instant_text") or DEFAULT_INSTANT_TEXT),
        "poll_seconds": max(5, int(section.get("poll_seconds") or 10)),
        "deep_reply": bool(section.get("deep_reply", True)),
        "direct_answer": bool(section.get("direct_answer", False)),
        "scan_interval_minutes": int(section.get("scan_interval_minutes") or 10),
        "history_limit": int(section.get("history_limit") or 20),
        "max_students": int(section.get("max_students") or 40),
        "chrome_debug_port": int(section.get("chrome_debug_port") or 9223),
    }


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def run_scan(history_limit: int | None = None, scan_all: bool = False, timeout: int = 600) -> dict[str, Any]:
    """调用 CDP 脚本拉取学员全景 + 消息 + 题目，落到 data/im-overview.json。"""
    settings = im_ai_settings()
    limit = history_limit or settings["history_limit"]
    cmd = [
        "node",
        str(SCRIPTS_DIR / "fetch_im_overview.mjs"),
        f"--port={settings['chrome_debug_port']}",
        "--page=user-board",
        f"--out={OVERVIEW_PATH}",
        f"--history-limit={limit}",
        f"--max-students={settings['max_students']}",
        "--with-questions=1",
    ]
    if scan_all:
        cmd.append("--all=1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"扫描超时（{timeout}s）"}
    if proc.returncode != 0:
        detail = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": False, "error": "扫描脚本失败", "detail": detail.strip()[:800]}
    if not OVERVIEW_PATH.exists():
        return {"ok": False, "error": "扫描未产出数据文件"}
    try:
        data = json.loads(OVERVIEW_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        return {"ok": False, "error": f"读取扫描结果失败: {error}"}
    return {"ok": True, "summary": (proc.stdout or "").strip(), "overview": data}


def poll_candidates(timeout: int = 60) -> dict[str, Any]:
    """轻量轮询：只拿「最后一条来自学生」的学员和他们的最近消息（约 1 秒）。"""
    settings = im_ai_settings()
    cmd = [
        "node",
        str(SCRIPTS_DIR / "poll_im_new_messages.mjs"),
        f"--port={settings['chrome_debug_port']}",
        "--page=user-board",
        "--history-limit=8",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"轮询超时（{timeout}s）"}
    raw = (proc.stdout or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "error": "轮询结果无法解析", "detail": (raw or (proc.stderr or ""))[:400]}


def fetch_student_questions(
    user_id: int, term_id: int, class_id: int = 0, timeout: int = 60
) -> dict[str, Any]:
    """取某个学员最新已解锁课程的作答与代码（约 1 秒）。"""
    settings = im_ai_settings()
    cmd = [
        "node",
        str(SCRIPTS_DIR / "fetch_student_questions.mjs"),
        f"--port={settings['chrome_debug_port']}",
        "--page=user-board",
        f"--user-id={int(user_id)}",
        f"--term-id={int(term_id)}",
    ]
    if class_id:
        cmd.append(f"--class-id={int(class_id)}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"取题超时（{timeout}s）"}
    raw = (proc.stdout or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "error": "取题结果无法解析", "detail": (raw or (proc.stderr or ""))[:400]}


# --------------------------------------------------------------------------- #
# 识别
# --------------------------------------------------------------------------- #
def message_text(msg: dict[str, Any]) -> str:
    """把一条消息压成可读文本。"""
    text = str(msg.get("text") or "").strip()
    if not text:
        text = str(msg.get("transerTxt") or "").strip()
    card = msg.get("card") or {}
    if not text and isinstance(card, dict):
        text = str(card.get("stem") or card.get("title") or "").strip()
    if not text and msg.get("help"):
        text = "[学生点了求助]"
    if not text and msg.get("media"):
        media = msg["media"]
        text = "[语音通话]" if media.get("rtcId") else "[语音消息]"
    if not text:
        text = "[图片]"
    return text


def message_kind(msg: dict[str, Any]) -> str:
    if msg.get("help") or msg.get("from") == HELP_QUESTION_FROM or msg.get("msgType") == HELP_QUESTION_MSGTYPE:
        return "求助"
    if msg.get("media") and msg["media"].get("rtcId"):
        return "通话"
    if msg.get("msgType") == 2 or str(msg.get("text") or "").startswith("http"):
        return "图片"
    if msg.get("msgType") == 8:
        return "语音"
    return "文字"


def looks_like_question(text: str) -> bool:
    if not text:
        return False
    if any(word in text for word in HELP_KEYWORDS):
        return True
    return text.rstrip().endswith(("?", "？", "吗", "呢"))


def collect_student_signals(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """从消息历史里挑出学生侧的提问/求助信号。"""
    student_msgs = [m for m in messages if m.get("from") in (STUDENT_FROM, HELP_QUESTION_FROM)]
    signals = []
    for msg in student_msgs:
        text = message_text(msg)
        kind = message_kind(msg)
        strong = kind in ("求助", "通话")
        if strong or looks_like_question(text):
            signals.append(
                {
                    "text": text,
                    "kind": kind,
                    "sendTime": msg.get("sendTime"),
                    "strong": strong,
                    "msgType": msg.get("msgType"),
                }
            )
    last = student_msgs[-1] if student_msgs else None
    return {
        "signals": signals,
        "lastStudentMessage": last,
        "lastStudentTime": (last or {}).get("sendTime") or 0,
        "studentMessageCount": len(student_msgs),
    }


def transcript(messages: list[dict[str, Any]], limit: int = 14) -> list[dict[str, Any]]:
    rows = []
    for msg in messages[-limit:]:
        who = "老师" if msg.get("from") == TEACHER_FROM else "学生"
        rows.append({"who": who, "kind": message_kind(msg), "text": message_text(msg)[:300], "t": msg.get("sendTime")})
    return rows


def _correct_from_options(item: dict[str, Any]) -> str:
    """normalAnswer 并不是标准答案，真正的答案在 options[].isCorrect 里。"""
    texts = [str(o.get("text") or "").strip() for o in (item.get("options") or []) if o.get("correct")]
    return " / ".join(t for t in texts if t)


def wrong_questions(question_payload: dict[str, Any] | None, max_items: int = 8) -> list[dict[str, Any]]:
    """挑出该学员最近课程里的练习题：带代码的 → 做错的 → 还没做的。"""
    if not question_payload:
        return []
    done_rows: list[dict[str, Any]] = []
    todo_rows: list[dict[str, Any]] = []
    for item in question_payload.get("items") or []:
        code = str(item.get("code") or "").strip()
        right = item.get("right")
        user_answer = str(item.get("userAnswer") or "").strip()
        row = {
            "name": item.get("name") or "",
            "description": (item.get("description") or "")[:400],
            "userAnswer": user_answer[:400],
            "correct": _correct_from_options(item) or str(item.get("normalAnswer") or "")[:200],
            "right": right,
            "code": code[:1500],
            "knowledge": item.get("knowledge") or [],
            "attempted": bool(user_answer or code),
        }
        if code or user_answer:
            done_rows.append(row)
        else:
            todo_rows.append(row)

    # 带代码的排最前，其次做错的
    done_rows.sort(key=lambda r: (0 if r["code"] else 1, 0 if r["right"] is False else 1))
    rows = done_rows[:max_items]
    # 学生什么都没做时，给几道最近课程的题当上下文（说明他卡在哪一课）
    if not rows:
        rows = todo_rows[:3]
    return rows


# --------------------------------------------------------------------------- #
# 豆包
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return None
    return None


def doubao_chat(messages: list[dict[str, str]], temperature: float = 0.3, timeout: int = 120) -> tuple[str, str]:
    env = load_doubao_env()
    if not env["api_key"] or not env["model"]:
        return "", "未配置豆包 API Key 或模型（config/doubao.env）"
    body = json.dumps(
        {"model": env["model"], "messages": messages, "temperature": temperature},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{env['base_url']}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {env['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        return "", f"豆包请求失败: {error}"
    choices = payload.get("choices") or []
    if not choices:
        return "", f"豆包返回异常: {json.dumps(payload, ensure_ascii=False)[:300]}"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    return content, ""


SYSTEM_PROMPT = (
    "你是一位经验丰富的少儿编程（C++/图形化）答疑助教，正在协助老师处理学生的课堂提问。"
    "你的回答会先给老师看，老师确认后再发给学生。"
    "要求：\n"
    "1. 用词亲切、口语化，像老师对学生说话，不要说教、不要堆术语。\n"
    "2. 先判断学生到底卡在哪一步，再给具体可执行的提示。\n"
    "3. 涉及代码时，指出出错的那一行/那个条件，说明为什么错、应该怎么改。\n"
    "4. 多鼓励，保护孩子的学习兴趣，不要否定孩子。\n"
    "5. 只依据给出的信息作答，不要编造学生没做过的题或成绩。\n"
    "只返回 JSON，不要 markdown 代码块。"
)


def build_prompt(task: dict[str, Any], direct_answer: bool = False) -> str:
    transcript_rows = task.get("transcript") or []
    chat_lines = [f"{row['who']}（{row['kind']}）：{row['text']}" for row in transcript_rows]
    signal_lines = [f"- {item['kind']}：{item['text']}" for item in (task.get("signals") or [])]
    question_lines = []
    for item in task.get("questions") or []:
        part = [f"题名：{item['name']}"]
        if item.get("description"):
            part.append(f"题目：{item['description']}")
        if item.get("code"):
            part.append(f"学生的代码：\n{item['code']}")
        elif item.get("userAnswer"):
            part.append(f"学生的答案：{item['userAnswer']}")
        if item.get("correct"):
            part.append(f"正确答案：{item['correct']}")
        if item.get("code"):
            part.append("状态：这题学生写了代码")
        elif item.get("userAnswer"):
            part.append("状态：这题学生做过")
        else:
            part.append("状态：这题学生还没有作答")
        if item.get("right") is False:
            part.append("判题结果：做错了")
        question_lines.append("\n".join(part))

    answer_rule = (
        "可以直接给出可运行的完整代码，但要先说明关键改动点。"
        if direct_answer
        else "不要直接甩完整答案，用「你试试把 X 改成 Y」这种方式引导，最多给关键一两行代码。"
    )
    return (
        f"学生姓名：{task.get('name') or '同学'}\n"
        f"当前课程：{task.get('courseName') or '未知'}\n"
        f"最近活动时间：{task.get('lastActiveText') or '未知'}\n\n"
        f"【学生发出的问题/求助信号】\n" + ("\n".join(signal_lines) or "（无明确文字，仅从对话推断）") + "\n\n"
        f"【最近对话】\n" + ("\n".join(chat_lines) or "（无）") + "\n\n"
        f"【该生最近的做题情况（可能为空）】\n" + ("\n---\n".join(question_lines) or "（暂未取到）") + "\n\n"
        f"【回复风格】{answer_rule}\n\n"
        "请返回 JSON，字段：\n"
        '{"summary":"一句话概括学生现在的问题（20字内）",'
        '"category":"代码报错|题目不会|概念不懂|求助求关注|其他",'
        '"urgency":"高|中|低",'
        '"analysis":"给老师看的分析：学生卡在哪、可能的原因、建议怎么讲（120字内）",'
        '"reply":"可以直接发给学生的回复（80字内，亲切口语）",'
        '"follow_up":"如果学生还不懂，下一步可以怎么引导（40字内）"}'
    )


def analyze_task(task: dict[str, Any]) -> dict[str, Any]:
    settings = im_ai_settings()
    prompt = build_prompt(task, direct_answer=settings["direct_answer"])
    content, error = doubao_chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    if error:
        task["ai"] = {"ok": False, "error": error}
        return task
    parsed = extract_json(content)
    if not parsed:
        task["ai"] = {"ok": False, "error": "豆包返回无法解析", "raw": content[:600]}
        return task
    task["ai"] = {
        "ok": True,
        "summary": str(parsed.get("summary") or "").strip(),
        "category": str(parsed.get("category") or "").strip(),
        "urgency": str(parsed.get("urgency") or "").strip(),
        "analysis": str(parsed.get("analysis") or "").strip(),
        "reply": str(parsed.get("reply") or "").strip(),
        "follow_up": str(parsed.get("follow_up") or "").strip(),
        "model": load_doubao_env()["model"],
        "generatedAt": int(__import__("time").time() * 1000),
    }
    return task


# --------------------------------------------------------------------------- #
# 状态与任务
# --------------------------------------------------------------------------- #
def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"students": {}}


def save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_tasks() -> dict[str, Any]:
    if TASKS_PATH.exists():
        try:
            data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("tasks"), list):
                return data
        except Exception:
            pass
    return {"updatedAt": 0, "tasks": []}


def save_tasks(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fmt_time(ms: int | None) -> str:
    if not ms:
        return ""
    import time as _time

    return _time.strftime("%m-%d %H:%M", _time.localtime(ms / 1000))


# 「纯应答」词表：孩子只是应答一声，没有新的问题，不需要 AI 追答。
# 判定用完全匹配（去掉标点后），避免把「好的老师，但是这题我不会」误判成纯应答。
PURE_ACK_WORDS = {
    "好", "好的", "好滴", "好哒", "好嘞", "好呀", "好的呀", "好的老师", "好滴老师", "好嘞老师",
    "可以", "可以的", "行", "行的", "没问题", "没有问题了", "嗯", "嗯嗯", "嗯呐", "嗯呢",
    "哦", "噢", "喔", "哦哦", "知道", "知道了", "明白", "明白了", "了解", "了解了",
    "收到", "收到啦", "收到了", "谢谢", "谢谢老师", "谢谢啦", "感谢", "感谢老师", "多谢", "谢了",
    "好嘞谢谢", "好的谢谢", "好的谢谢老师", "3q", "thx", "thanks", "thankyou",
    "ok", "okay", "ok啦", "ok的", "okk", "okok", "可以可以", "对对", "对的", "是的", "是滴",
}


def is_pure_acknowledgement(msg: dict[str, Any]) -> bool:
    """判断这条消息是不是「纯应答」（好的 / ok / 谢谢 这类），这类不需要 AI 追答。

    图片、语音、通话、求助卡片一律不算纯应答——孩子发截图往往正是要问题。
    """
    if msg.get("from") == HELP_QUESTION_FROM or msg.get("msgType") == HELP_QUESTION_MSGTYPE:
        return False
    if msg.get("media") or msg.get("msgType") in (2, 8):
        return False
    text = str(msg.get("text") or msg.get("transerTxt") or "").strip()
    if not text:
        return False
    # 去掉空白、标点、emoji，只留下文字部分再比对
    compact = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()
    if not compact:
        return False
    if compact in PURE_ACK_WORDS:
        return True
    # 「行了 / 好啦 / 可以了 / 谢谢了」这类：去掉末尾语气助词再比。
    # 「我不会了」去掉「了」后是「我不会」，不在词表里，所以不会被误判。
    trimmed = compact.rstrip("了啦哒滴呀哦喔嘞呢吧哈嘛噢")
    if trimmed and trimmed in PURE_ACK_WORDS:
        return True
    # 「收到，谢谢老师」这类由多个应答词拼起来的：逐个剥掉应答词，剥不干净就不算。
    # 用最长优先，保证「好的老师但是这题我不会」剥掉「好的老师」后还剩内容 -> 不是纯应答。
    ordered = sorted(PURE_ACK_WORDS, key=len, reverse=True)
    rest = compact
    while rest:
        hit = next((word for word in ordered if rest.startswith(word)), None)
        if hit is None:
            return False
        rest = rest[len(hit):]
    return True


def _today_start_ms() -> int:
    """今天 00:00 的毫秒时间戳。"""
    import time as _time

    t = _time.localtime()
    return int(_time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)


def pending_reply(messages: list[dict[str, Any]], day_start_ms: int | None = None) -> dict[str, Any] | None:
    """判断这个学员是不是「今天问了、老师还没回」。

    判据很简单：最后一条消息是学生发的，而且就发生在今天。
    只要老师回过（最后一条是老师），或者学生是昨天以前问的，都不算。
    """
    if not messages:
        return None
    last = messages[-1]
    if last.get("from") not in (STUDENT_FROM, HELP_QUESTION_FROM):
        return None
    sent_at = int(last.get("sendTime") or 0)
    if day_start_ms is None:
        day_start_ms = _today_start_ms()
    if sent_at < day_start_ms:
        return None
    return last


def build_tasks(
    overview: dict[str, Any],
    state: dict[str, Any],
    prior_tasks: list[dict[str, Any]] | None = None,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    """把扫描结果变成待处理任务：今天学生说了话、老师还没回的学员。"""
    students = {str(s.get("userId")): s for s in overview.get("students") or []}
    messages = overview.get("messages") or {}
    questions = overview.get("questions") or {}
    seen = state.get("students") or {}
    prior = {str(t.get("userId")): t for t in (prior_tasks or [])}
    tasks: list[dict[str, Any]] = []
    day_start = _today_start_ms()

    for uid_key, msgs in messages.items():
        student = students.get(str(uid_key))
        if not student:
            continue
        info = collect_student_signals(msgs or [])
        signals = info["signals"]
        pending = pending_reply(msgs or [], day_start)
        if not include_all and not pending:
            continue
        last_time = int((pending or info.get("lastStudentMessage") or {}).get("sendTime") or 0)
        previous = int((seen.get(str(uid_key)) or {}).get("lastStudentTime") or 0)
        is_new = last_time > previous
        old = prior.get(str(uid_key)) or {}
        # 老师已经点过「已处理」、学生又没再说话，就不再冒出来
        handled = bool(old) and old.get("status") == "done" and not is_new
        if handled and not include_all:
            continue

        # 只保留最近一批信号（越靠后越相关）；确保「最后那条待回复的消息」一定在列表里
        recent_signals = signals[-4:]
        if pending and not any(item.get("sendTime") == pending.get("sendTime") for item in recent_signals):
            recent_signals = recent_signals + [
                {
                    "text": message_text(pending),
                    "kind": message_kind(pending),
                    "sendTime": pending.get("sendTime"),
                    "strong": pending.get("from") == HELP_QUESTION_FROM,
                    "msgType": pending.get("msgType"),
                }
            ]
        has_strong = any(item["strong"] for item in recent_signals)
        task = {
            "userId": student.get("userId"),
            "name": student.get("name") or "",
            "imId": student.get("imId") or "",
            "termId": student.get("termId"),
            "classId": student.get("classId"),
            "className": student.get("className") or "",
            "termName": student.get("termName") or "",
            "unreadCount": student.get("unreadCount") or 0,
            "trigger": "求助卡片" if has_strong else "提问",
            "signals": recent_signals,
            "transcript": transcript(msgs or []),
            "lastStudentTime": last_time,
            "lastActiveText": _fmt_time(last_time),
            "studentMessageCount": info["studentMessageCount"],
            "courseName": (questions.get(str(uid_key)) or {}).get("courseName") or "",
            "questions": wrong_questions(questions.get(str(uid_key))),
            "isNew": is_new,
            "status": "new" if is_new else str(old.get("status") or "new"),
            "createdAt": int(__import__("time").time() * 1000),
        }
        tasks.append(task)

    tasks.sort(key=lambda t: (0 if t["trigger"] == "求助卡片" else 1, -(t.get("lastStudentTime") or 0)))
    return tasks


def send_im_message(
    user_id: int,
    term_id: int,
    text: str,
    im_id: str = "",
    dry: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    """两步发送：先走 IM 通道真实投递拿到 originId，再上报到 CRM。

    只上报不投递 -> 学生收不到、也没有 originId（撤不回），所以两步缺一不可。
    """
    text = str(text or "").strip()
    if not user_id or not term_id:
        return {"ok": False, "error": "缺少学员 userId 或期次 termId，请重新扫描"}
    if not text:
        return {"ok": False, "error": "回复内容为空"}

    settings = im_ai_settings()
    cmd = [
        "node",
        str(SCRIPTS_DIR / "send_im_via_channel.mjs"),
        f"--port={settings['chrome_debug_port']}",
        "--page=user-board",
        f"--user-id={int(user_id)}",
        f"--term-id={int(term_id)}",
        f"--text={text}",
    ]
    if im_id:
        cmd.append(f"--im-id={im_id}")
    if dry:
        cmd.append("--dry=1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"发送超时（{timeout}s）"}

    raw = (proc.stdout or "").strip()
    try:
        result = json.loads(raw)
    except Exception:
        return {
            "ok": False,
            "error": "无法解析发送结果",
            "detail": (raw or (proc.stderr or ""))[:500],
        }
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "发送失败"), "detail": result}
    return {
        "ok": True,
        "dry": bool(result.get("dry")),
        "msgId": result.get("msgId") or "",
        "originId": result.get("originId") or "",
    }


def send_task_reply(user_id: int, text: str, dry: bool = False) -> dict[str, Any]:
    """发送某个学员任务里的回复，成功后把任务标记为已处理。"""
    payload = load_tasks()
    tasks = payload.get("tasks") or []
    target = next((t for t in tasks if int(t.get("userId") or 0) == int(user_id)), None)
    if not target:
        return {"ok": False, "error": "没有找到该学员的任务，请先扫描"}

    content = str(text or "").strip()
    if not content:
        content = str((target.get("ai") or {}).get("reply") or "").strip()
    if not content:
        return {"ok": False, "error": "没有可发送的回复内容"}

    outcome = send_im_message(
        int(target.get("userId") or 0),
        int(target.get("termId") or 0),
        content,
        im_id=str(target.get("imId") or ""),
        dry=dry,
    )
    if not outcome.get("ok"):
        return outcome

    if not dry:
        sends = target.setdefault("sends", [])
        sends.append(
            {
                "text": content,
                "at": int(__import__("time").time() * 1000),
                "msgId": outcome.get("msgId") or "",
                "originId": outcome.get("originId") or "",
            }
        )
        target["status"] = "done"
        payload["updatedAt"] = int(__import__("time").time() * 1000)
        save_tasks(payload)
    return {**outcome, "userId": user_id, "name": target.get("name") or ""}


def student_conversation(user_id: int) -> dict[str, Any]:
    """从最近一次扫描结果里取某个学员的对话，给 IM 页面用。"""
    if not OVERVIEW_PATH.exists():
        return {"ok": False, "error": "还没有扫描结果，请先点「立即扫描」"}
    try:
        overview = json.loads(OVERVIEW_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        return {"ok": False, "error": f"读取扫描结果失败：{error}"}

    messages = (overview.get("messages") or {}).get(str(user_id)) or []
    student = next(
        (s for s in overview.get("students") or [] if str(s.get("userId")) == str(user_id)),
        None,
    )
    rows = [
        {
            "who": "老师" if m.get("from") == TEACHER_FROM else "学生",
            "kind": message_kind(m),
            "text": message_text(m),
            "time": m.get("sendTime"),
        }
        for m in messages
    ]
    return {"ok": True, "student": student, "messages": rows}


def set_task_status(user_id: int, status: str) -> dict[str, Any]:
    """把某个学员的任务标记为已处理 / 重新打开。"""
    payload = load_tasks()
    tasks = payload.get("tasks") or []
    target = next((t for t in tasks if int(t.get("userId") or 0) == int(user_id)), None)
    if not target:
        return {"ok": False, "error": "没有找到该学员的任务，请先扫描"}
    target["status"] = "done" if status == "done" else "new"
    payload["updatedAt"] = int(__import__("time").time() * 1000)
    save_tasks(payload)
    return {"ok": True, "userId": user_id, "status": target["status"]}


def scan_im(analyze: bool = True, scan_all: bool = False) -> dict[str, Any]:
    """扫描 + 识别 + （可选）豆包分析，并把结果写入 data/im-ai-tasks.json。"""
    result = run_scan(scan_all=scan_all)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"), "detail": result.get("detail")}

    overview = result["overview"]
    state = load_state()
    previous_tasks = load_tasks().get("tasks") or []
    tasks = build_tasks(overview, state, prior_tasks=previous_tasks, include_all=scan_all)

    # 保留历史任务里已分析过的结论，避免重复烧 token
    previous = {str(t.get("userId")): t for t in previous_tasks}
    for task in tasks:
        old = previous.get(str(task["userId"]))
        if old and old.get("ai", {}).get("ok") and int(old.get("lastStudentTime") or 0) == int(task["lastStudentTime"] or 0):
            task["ai"] = old["ai"]

    analyzed = 0
    if analyze:
        for task in tasks:
            if task.get("ai", {}).get("ok"):
                continue
            analyze_task(task)
            if task.get("ai", {}).get("ok"):
                analyzed += 1

    # 更新扫描状态
    for task in tasks:
        seen = state.setdefault("students", {})
        uid = str(task["userId"])
        entry = seen.setdefault(uid, {})
        entry["lastStudentTime"] = max(int(entry.get("lastStudentTime") or 0), int(task["lastStudentTime"] or 0))
        entry["name"] = task["name"]
    state["lastScanAt"] = int(__import__("time").time() * 1000)
    save_state(state)

    payload = {
        "updatedAt": int(__import__("time").time() * 1000),
        "scannedAt": overview.get("scannedAt"),
        "studentCount": len(overview.get("students") or []),
        "allUnreadCount": overview.get("allUnreadCount") or 0,
        "targetCount": len(overview.get("targetIds") or []),
        "tasks": tasks,
        "errors": overview.get("errors") or [],
    }
    save_tasks(payload)
    return {
        "ok": True,
        "studentCount": payload["studentCount"],
        "allUnreadCount": payload["allUnreadCount"],
        "taskCount": len(tasks),
        "analyzed": analyzed,
        "summary": result.get("summary") or "",
    }


def orphaned_questions(question_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    return wrong_questions(question_payload)


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}") if not sys.stdin.isatty() else {}
    print(json.dumps(scan_im(analyze=bool(payload.get("analyze", True))), ensure_ascii=False))
