"""Create one CRM/WeCom pending task for a National Day makeup-plan image."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from create_monthly_exam_task import request_headers, save_json, upload_file


DEFAULT_MESSAGE = "这是给孩子整理的国庆补课计划哈，假期可以按图片里的安排补一下未完成课程，每天完成后截图打卡即可～"


def configured_message(workspace: Path) -> str:
    config_path = workspace / "data" / "teacher-workbench-config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        settings = payload.get("national_day_makeup") if isinstance(payload.get("national_day_makeup"), dict) else {}
        message = str(settings.get("message") or "").strip()
        return message or DEFAULT_MESSAGE
    except Exception:
        return DEFAULT_MESSAGE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    matches = [item for item in manifest.get("items") or [] if str(item.get("student_id")) == str(args.student_id)]
    if len(matches) != 1:
        raise RuntimeError(f"清单中学生ID {args.student_id} 出现 {len(matches)} 次，已停止")
    item = matches[0]
    student_id = int(item["student_id"])
    student_name = str(item.get("name") or student_id)
    image = Path(str(item.get("image") or ""))
    if not image.is_absolute():
        image = (args.workspace / image).resolve()
    if not image.is_file():
        raise RuntimeError(f"国庆补课计划图片不存在：{image}")
    message = configured_message(args.workspace)

    scripts_dir = args.workspace.resolve() / "scripts"
    sender_path = scripts_dir / "send_week1_personalized_feedback.py"
    if not sender_path.is_file():
        raise RuntimeError(f"工作台缺少企微发送模块：{sender_path}")
    sys.path.insert(0, str(scripts_dir))
    sender = importlib.import_module("send_week1_personalized_feedback")
    requests = importlib.import_module("requests")
    crm = sender.load_crm_module()
    config = crm.read_json(sender.CONFIG_PATH)
    config["cookies_file"] = str(sender.refresh_crm_cookies())
    profile_crm = sender.CONFIG_PROFILE.get("crm") if isinstance(sender.CONFIG_PROFILE.get("crm"), dict) else {}
    if int(profile_crm.get("class_pool_id") or 0) > 0:
        config["class_pool_id"] = int(profile_crm["class_pool_id"])
    config["classes"] = [
        {"name": value["slot"], "term_id": value["term_id"], "class_id": value["class_id"]}
        for value in sender.real_class_lookup().values()
    ]
    config["defaults"]["exclude_task_object_list"] = [{"code": 232, "name": "已请假", "type": 0}]
    client = crm.CrmClient(config)

    classified = client.classify_users([student_id])
    class_id = None
    for group in classified if isinstance(classified, list) else []:
        if student_id in {int(value) for value in group.get("userIds") or []}:
            if class_id is not None:
                raise RuntimeError("目标学员被归入多个班级，已停止")
            class_id = int(group["classId"])
    class_item = crm.class_lookup(config).get(class_id or 0)
    if not class_item:
        raise RuntimeError("目标学员无法唯一归入当前老师配置的班级")
    wx_users = client.user_wechat_info(class_id, [student_id])
    sendable = [value for value in wx_users if int(value.get("userId") or 0) == student_id and value.get("externalUserIds")]
    external_user_ids: list[str] = []
    seen: set[str] = set()
    for value in sendable:
        for external_id in value.get("externalUserIds") or []:
            external_text = str(external_id).strip()
            if external_text and external_text not in seen:
                seen.add(external_text)
                external_user_ids.append(external_text)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry-run",
        "student_id": str(student_id), "student_name": student_name,
        "image": str(image), "class_id": class_id, "term_id": int(class_item["term_id"]),
        "mapping_ok": bool(external_user_ids), "wechat_parent_count": len(external_user_ids), "created": False,
    }
    if not external_user_ids:
        result.update({"skipped": True, "skip_reason": "没有可发送的企微家长映射"})
        save_json(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    if not args.execute:
        save_json(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0

    uploaded = upload_file(requests, client, image, "image")
    now = int(time.time() * 1000)
    payload = {
        "termId": int(class_item["term_id"]), "classId": int(class_item["class_id"]),
        "users": [{"userId": student_id, "externalUserIds": external_user_ids}],
        "excludeUserList": [], "sendType": 3, "businessType": 0,
        "msgContents": [
            {"timeStamp": now, "type": 0, "check": True, "resourceContent": message, "sort": 0},
            {"timeStamp": now + 1, "type": 1, "check": True, "resourceContent": uploaded["url"], "resourceDescription": image.name, "size": image.stat().st_size, "sort": 1, "mediaId": uploaded["media_id"]},
        ],
        "tabType": "0", "hasStudy": False, "sendWechatType": 0, "sendingObject": 0,
        "excludeTaskObjectList": config["defaults"]["exclude_task_object_list"], "chooseUserList": [student_id],
    }
    response = client.send_notify(payload)
    if response.get("success") is not True and response.get("code") != 200:
        raise RuntimeError(f"CRM 创建待发送任务失败：{response.get('msg') or '未知错误'}")
    result.update({"created": True, "created_at": datetime.now().isoformat(timespec="seconds"), "response_message": response.get("msg") or "操作成功"})
    save_json(args.result, result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print("CRM pending task created; final sending still requires WeCom confirmation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
