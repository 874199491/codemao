"""Create one guarded CRM/WeCom monthly-exam feedback task with a PDF attachment."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

import send_week1_personalized_feedback as feedback_sender


WORKSPACE = Path(__file__).resolve().parents[1]


def save_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request_headers(client) -> dict[str, str]:
    headers = dict(client.headers)
    headers.pop("Content-Type", None)
    return headers


def upload_file(client, file_path: Path, material_type: str) -> dict[str, str]:
    stamp = int(time.time() * 1000)
    remote_name = f"monthly_exam_{stamp}{file_path.suffix.lower()}"
    query = urlencode(
        {
            "projectName": "crm_web_rocket",
            "filePaths": remote_name,
            "filePath": remote_name,
            "tokensCount": 1,
            "fileSign": "p1",
            "insertOnly": "true",
            "cdnName": "qiniu",
        }
    )
    token_url = f"https://open-service.codemao.cn/cdn/qi-niu/tokens/uploading?{query}"
    token_response = requests.get(token_url, headers=request_headers(client), timeout=60)
    token_response.raise_for_status()
    token_data = token_response.json()
    tokens = token_data.get("tokens") or []
    if len(tokens) != 1 or not tokens[0].get("token") or not tokens[0].get("file_path"):
        raise RuntimeError("上传凭证返回异常，未创建发送任务")

    token_item = tokens[0]
    upload_url = token_data.get("upload_url") or "https://upload.qiniup.com"
    mime_type = "application/pdf" if file_path.suffix.lower() == ".pdf" else "image/png"
    with file_path.open("rb") as source:
        upload_response = requests.post(
            upload_url,
            data={"token": token_item["token"], "key": token_item["file_path"]},
            files={"file": (file_path.name, source, mime_type)},
            timeout=180,
        )
    upload_response.raise_for_status()
    uploaded = upload_response.json()
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
    material_data = material.get("data") or {}
    if material.get("success") is not True or not material_data.get("media_id"):
        raise RuntimeError(f"企微素材登记失败：{material.get('msg') or '未知错误'}")
    return {"url": public_url, "media_id": material_data["media_id"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-id", required=True, type=int)
    parser.add_argument("--student-name", required=True)
    parser.add_argument("--score", required=True, type=int)
    parser.add_argument("--message-file", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    message = args.message_file.read_text(encoding="utf-8-sig").strip()
    if not message:
        raise RuntimeError("反馈话术为空")
    if args.student_name not in message or str(args.score) not in message:
        raise RuntimeError("话术中的姓名或分数与目标学员不一致")
    if not args.pdf.is_file():
        raise RuntimeError(f"错题报告不存在：{args.pdf}")
    if args.pdf.name != f"{args.student_name}_错题解析.pdf":
        raise RuntimeError("错题报告文件名与目标学员姓名不一致")
    if not args.image.is_file():
        raise RuntimeError(f"奖状不存在：{args.image}")
    if args.image.name != f"{args.student_name}_奖状.png":
        raise RuntimeError("奖状文件名与目标学员姓名不一致")

    crm = feedback_sender.load_crm_module()
    config = crm.read_json(feedback_sender.CONFIG_PATH)
    config["cookies_file"] = str(feedback_sender.refresh_crm_cookies())
    profile_crm = (
        feedback_sender.CONFIG_PROFILE.get("crm")
        if isinstance(feedback_sender.CONFIG_PROFILE.get("crm"), dict)
        else {}
    )
    if int(profile_crm.get("class_pool_id") or 0) > 0:
        config["class_pool_id"] = int(profile_crm["class_pool_id"])
    config["classes"] = [
        {
            "name": item["slot"],
            "term_id": item["term_id"],
            "class_id": item["class_id"],
        }
        for item in feedback_sender.real_class_lookup().values()
    ]
    config["defaults"]["exclude_task_object_list"] = [
        {"code": 232, "name": "已请假", "type": 0}
    ]

    client = crm.CrmClient(config)
    grouped = client.classify_users([args.student_id])
    class_id = None
    for group in grouped if isinstance(grouped, list) else []:
        if args.student_id in {int(value) for value in group.get("userIds") or []}:
            class_id = int(group["classId"])
            break
    class_item = crm.class_lookup(config).get(class_id or 0)
    if not class_item:
        raise RuntimeError("目标学员无法唯一归入当前老师配置的班级")

    wx_users = client.user_wechat_info(class_id, [args.student_id])
    sendable_users = [
        user
        for user in wx_users
        if int(user.get("userId") or 0) == args.student_id and user.get("externalUserIds")
    ]
    external_count = sum(len(user.get("externalUserIds") or []) for user in sendable_users)
    if len(sendable_users) != 1 or external_count != 1:
        raise RuntimeError(
            f"企微映射不是唯一可发送家长：学生记录 {len(sendable_users)}，家长映射 {external_count}"
        )

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry-run",
        "student_id": str(args.student_id),
        "student_name": args.student_name,
        "score": args.score,
        "class_id": class_id,
        "term_id": int(class_item["term_id"]),
        "pdf": str(args.pdf),
        "image": str(args.image),
        "mapping_ok": True,
        "created": False,
    }
    if not args.execute:
        save_result(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Dry run only; no file uploaded and no CRM task created.")
        return 0

    uploaded_pdf = upload_file(client, args.pdf, "file")
    uploaded_image = upload_file(client, args.image, "image")
    task_users = [
        {
            "userId": args.student_id,
            "externalUserIds": sendable_users[0]["externalUserIds"],
        }
    ]
    now = int(time.time() * 1000)
    payload = {
        "termId": int(class_item["term_id"]),
        "classId": int(class_item["class_id"]),
        "users": task_users,
        "excludeUserList": [],
        "sendType": 3,
        "businessType": 0,
        "msgContents": [
            {
                "timeStamp": now,
                "type": 0,
                "check": True,
                "resourceContent": message,
                "sort": 0,
            },
            {
                "timeStamp": now + 1,
                "type": 4,
                "check": True,
                "resourceContent": uploaded_pdf["url"],
                "resourceDescription": args.pdf.name,
                "size": args.pdf.stat().st_size,
                "sort": 1,
                "mediaId": uploaded_pdf["media_id"],
            },
            {
                "timeStamp": now + 2,
                "type": 1,
                "check": True,
                "resourceContent": uploaded_image["url"],
                "resourceDescription": args.image.name,
                "size": args.image.stat().st_size,
                "sort": 2,
                "mediaId": uploaded_image["media_id"],
            },
        ],
        "tabType": "0",
        "hasStudy": False,
        "sendWechatType": 0,
        "sendingObject": 0,
        "excludeTaskObjectList": config["defaults"]["exclude_task_object_list"],
        "chooseUserList": [args.student_id],
    }
    response = client.send_notify(payload)
    if response.get("success") is not True and response.get("code") != 200:
        raise RuntimeError(f"CRM 创建待发送任务失败：{response.get('msg') or '未知错误'}")
    result.update(
        {
            "created": True,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "response_message": response.get("msg") or "操作成功",
            "attachment_name": args.pdf.name,
            "image_name": args.image.name,
        }
    )
    save_result(args.result, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("One CRM pending task created; final sending still requires WeCom confirmation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
