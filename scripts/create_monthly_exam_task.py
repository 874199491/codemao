"""Create one guarded CRM/WeCom pending task from a reviewed exam manifest."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request_headers(client) -> dict[str, str]:
    headers = dict(client.headers)
    headers.pop("Content-Type", None)
    return headers


def upload_file(requests, client, file_path: Path, material_type: str) -> dict[str, str]:
    stamp = int(time.time() * 1000)
    remote_name = f"monthly_exam_{stamp}_{file_path.name}"
    query = urlencode(
        {
            "projectName": "crm_web_rocket", "filePaths": remote_name, "filePath": remote_name,
            "tokensCount": 1, "fileSign": "p1", "insertOnly": "true", "cdnName": "qiniu",
        }
    )
    token_response = requests.get(
        f"https://open-service.codemao.cn/cdn/qi-niu/tokens/uploading?{query}",
        headers=request_headers(client), timeout=60,
    )
    token_response.raise_for_status()
    token_data = token_response.json()
    tokens = token_data.get("tokens") or []
    if len(tokens) != 1 or not tokens[0].get("token") or not tokens[0].get("file_path"):
        raise RuntimeError("上传凭证返回异常，未创建发送任务")
    token = tokens[0]
    mime = "application/pdf" if file_path.suffix.lower() == ".pdf" else "image/png"
    with file_path.open("rb") as source:
        response = requests.post(
            token_data.get("upload_url") or "https://upload.qiniup.com",
            data={"token": token["token"], "key": token["file_path"]},
            files={"file": (file_path.name, source, mime)}, timeout=180,
        )
    response.raise_for_status()
    uploaded = response.json()
    if not uploaded.get("key"):
        raise RuntimeError(f"{file_path.name} 上传失败，未创建发送任务")
    public_url = f"{token_data['bucket_url'].rstrip('/')}/{uploaded['key']}"
    material_response = requests.post(
        f"{client.lbk_base}/work-wechat/uploadMaterialFromUrl",
        headers=request_headers(client),
        data={"url": public_url, "fileName": file_path.name, "type": material_type},
        timeout=180,
    )
    material_response.raise_for_status()
    material = material_response.json()
    data = material.get("data") or {}
    if material.get("success") is not True or not data.get("media_id"):
        raise RuntimeError(f"企微素材登记失败：{material.get('msg') or '未知错误'}")
    return {"url": public_url, "media_id": data["media_id"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    matches = [item for item in manifest.get("students") or [] if str(item.get("student_id")) == str(args.student_id)]
    if len(matches) != 1:
        raise RuntimeError(f"清单中学生ID {args.student_id} 出现 {len(matches)} 次，已停止")
    item = matches[0]
    if manifest.get("roster_verified") is not True or item.get("roster_verified") is not True:
        raise RuntimeError("清单未通过当前老师学员名单校验，仅可预览")
    if item.get("send_ready") is not True or item.get("blockers"):
        raise RuntimeError("该学生仍有未解决校验项：" + "；".join(item.get("blockers") or []))

    student_id = int(item["student_id"])
    student_name = str(item["student_name"])
    score = item["score"]
    message = str(item.get("message") or "").strip()
    if not message or student_name not in message or str(int(score) if float(score).is_integer() else score) not in message:
        raise RuntimeError("反馈话术中的姓名或分数与目标学员不一致")
    pdf = Path(item["pdf"]) if item.get("pdf") else None
    award = Path(item["award"]) if item.get("award") else None
    if pdf and (not pdf.is_file() or pdf.name != f"{student_name}_错题解析.pdf"):
        raise RuntimeError("错题报告不存在或文件名与学生姓名不一致")
    if award and (not award.is_file() or award.name != f"{student_name}_奖状.png"):
        raise RuntimeError("奖状不存在或文件名与学生姓名不一致")

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
    external_count = sum(len(value.get("externalUserIds") or []) for value in sendable)
    if len(sendable) != 1 or external_count != 1:
        raise RuntimeError(f"企微映射不是唯一可发送家长：学生记录 {len(sendable)}，家长映射 {external_count}")

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry-run", "student_id": str(student_id),
        "student_name": student_name, "score": score, "class_id": class_id,
        "term_id": int(class_item["term_id"]), "pdf": str(pdf or ""), "award": str(award or ""),
        "mapping_ok": True, "created": False,
    }
    if not args.execute:
        save_json(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Dry run only; no file uploaded and no CRM task created.")
        return 0

    attachments: list[dict] = []
    if pdf:
        uploaded = upload_file(requests, client, pdf, "file")
        attachments.append({"path": pdf, "uploaded": uploaded, "type": 4})
    if award:
        uploaded = upload_file(requests, client, award, "image")
        attachments.append({"path": award, "uploaded": uploaded, "type": 1})
    now = int(time.time() * 1000)
    contents = [{"timeStamp": now, "type": 0, "check": True, "resourceContent": message, "sort": 0}]
    for sort, attachment in enumerate(attachments, start=1):
        path = attachment["path"]
        contents.append(
            {
                "timeStamp": now + sort, "type": attachment["type"], "check": True,
                "resourceContent": attachment["uploaded"]["url"], "resourceDescription": path.name,
                "size": path.stat().st_size, "sort": sort, "mediaId": attachment["uploaded"]["media_id"],
            }
        )
    payload = {
        "termId": int(class_item["term_id"]), "classId": int(class_item["class_id"]),
        "users": [{"userId": student_id, "externalUserIds": sendable[0]["externalUserIds"]}],
        "excludeUserList": [], "sendType": 3, "businessType": 0, "msgContents": contents,
        "tabType": "0", "hasStudy": False, "sendWechatType": 0, "sendingObject": 0,
        "excludeTaskObjectList": config["defaults"]["exclude_task_object_list"], "chooseUserList": [student_id],
    }
    response = client.send_notify(payload)
    if response.get("success") is not True and response.get("code") != 200:
        raise RuntimeError(f"CRM 创建待发送任务失败：{response.get('msg') or '未知错误'}")
    result.update({"created": True, "created_at": datetime.now().isoformat(timespec="seconds"), "response_message": response.get("msg") or "操作成功"})
    save_json(args.result, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("CRM pending task created; final sending still requires WeCom confirmation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise


