import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reminder_server_test", ROOT / "apps/teacher_workbench/server.py")
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)
import send_completion_reminder as sender


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / "completion.json"
        self.source.write_text(json.dumps({"targetWeek": 7, "fetchedAt": "today", "detailRows": [{"id": "fixture"}]}), encoding="utf-8")
        self.config = server.normalize_config({"solitaire_lookback_days": 1, "completion_reminders": {"absent": "请及时到课", "arrived_unfinished": "请完成剩余课程"}})
        self.patches = [patch.object(server, "load_config", return_value=self.config), patch.object(server, "script_config", return_value={}), patch.object(server, "completion_payload_paths", return_value=[self.source]), patch.object(server, "roster_and_refunds", return_value=({}, set())), patch.object(server, "build_completion_metrics", return_value={"metrics": [{"id": key, "label": key, "students": [{"id": sid, "name": "测试学员", "class_time": "周五"}]} for key, sid in [("absent", "101"), ("arrived_unfinished", "202")]]}), patch.object(server, "WORKSPACE", self.folder), patch.object(server, "JOBS", server.JobStore())]
        for item in self.patches: item.start()
        server.REMINDER_PREVIEWS.clear()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_config_roundtrip(self):
        with patch.object(server, "CONFIG_PATH", self.folder / "config.json"):
            saved = server.save_config({"completion_reminders": {"absent": "新正文"}})
            public = server.public_config(saved)
            self.assertEqual(public["completion_reminders"]["absent"], "新正文")
            self.assertEqual(public["completion_reminders"]["arrived_unfinished"], "请完成剩余课程")
            self.assertEqual(public["solitaire_lookback_days"], 1)

    def test_each_group_and_deduplication(self):
        for kind, sid in [("absent", "101"), ("arrived_unfinished", "202")]:
            preview = server.preview_completion_reminder({"kind": kind, "student_ids": [sid, sid]})
            self.assertEqual(preview["count"], 1)
            self.assertEqual(preview["message"], self.config["completion_reminders"][kind])

    def test_invalid_scope_and_empty_message(self):
        for payload in [{"kind": "finished", "student_ids": ["101"]}, {"kind": "absent", "student_ids": []}, {"kind": "absent", "student_ids": ["202"]}]:
            with self.assertRaises(ValueError): server.preview_completion_reminder(payload)
        self.config["completion_reminders"]["absent"] = ""
        with self.assertRaises(ValueError): server.preview_completion_reminder({"kind": "absent", "student_ids": ["101"]})

    def test_changed_template_rejected(self):
        preview = server.preview_completion_reminder({"kind": "absent", "student_ids": ["101"]})
        self.config["completion_reminders"]["absent"] = "已改动"
        with self.assertRaises(ValueError): server.send_completion_reminder({"token": preview["token"]})

    def test_same_token_creates_one_job(self):
        preview = server.preview_completion_reminder({"kind": "absent", "student_ids": ["101"]})
        with patch.object(server.threading, "Thread") as thread:
            first = server.send_completion_reminder({"token": preview["token"]})
            second = server.send_completion_reminder({"token": preview["token"]})
            self.assertEqual(first["job"]["id"], second["job"]["id"])
            self.assertEqual(thread.call_count, 1)
        manifest = json.loads(next(self.folder.glob("data/completion-reminders/*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual(manifest["student_ids"], ["101"])
        self.assertEqual(manifest["message"], "请及时到课")

    def test_dry_run_never_calls_crm(self):
        manifest = server.reminder_snapshot({"kind": "absent", "student_ids": ["101"]})
        path = self.folder / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(sender.subprocess, "run") as run:
            sender.run(path)
            run.assert_not_called()

    def test_sender_uses_resolved_course_ids(self):
        manifest = server.reminder_snapshot({"kind": "absent", "student_ids": ["101"]})
        path = self.folder / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if command[0] == "node":
                output = Path(command[command.index("--out-json") + 1])
                output.write_text(json.dumps({"courseId": 12300 + len(calls), "detailCount": 1}), encoding="utf-8")
        with patch.object(sender.subprocess, "run", side_effect=fake_run): sender.run(path, True)
        self.assertEqual(calls[-1][calls[-1].index("--course-id") + 1], "12302")
        self.assertIn(str(self.folder / "result.json"), calls[-1])
        self.assertEqual(len(calls), 3)


class CompletionMetricMakeupTests(unittest.TestCase):
    def test_scheduled_makeup_time_is_visible_but_does_not_change_completion_status(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            data_dir = folder / "data"
            data_dir.mkdir()
            (data_dir / "0724-makeup-time-archive.json").write_text(
                json.dumps({"101": "周六晚 19:00"}),
                encoding="utf-8",
            )
            config = server.normalize_config(
                {
                    "cohort_code": "0724",
                    "has_exam_training_lessons": False,
                    "profile": {
                        "data_prefix": "0724",
                        "classes": [{"class_id": 130020, "label": "周五晚", "match_prefix": "周五"}],
                    },
                }
            )
            roster = {"101": {"学生姓名": "测试学员", "上课时间": "周五晚", "班级": "周五晚"}}
            payload = {
                "targetWeek": 7,
                "detailRows": [
                    {"userId": "101", "classId": 130020, "lessonSort": 13, "status": "无数据", "childName": "测试学员"},
                    {"userId": "101", "classId": 130020, "lessonSort": 14, "status": "无数据", "childName": "测试学员"},
                ],
            }
            with patch.object(server, "WORKSPACE", folder):
                metrics = server.build_completion_metrics(payload, roster, set(), config)["metrics"]
            absent = next(item for item in metrics if item["id"] == "absent")
            all_students = next(item for item in metrics if item["id"] == "all")["students"]
            self.assertEqual(absent["students"][0]["id"], "101")
            self.assertEqual(all_students[0]["status"], "未到课")
            self.assertEqual(absent["students"][0]["makeup_time"], "周六晚 19:00")

    def test_learning_active_ids_filter_stale_roster_students(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            data_dir = folder / "data"
            data_dir.mkdir()
            (data_dir / "0724-learning-active-ids.json").write_text(
                json.dumps({"student_ids": ["101"]}),
                encoding="utf-8",
            )
            config = server.normalize_config(
                {
                    "cohort_code": "0724",
                    "has_exam_training_lessons": False,
                    "profile": {
                        "data_prefix": "0724",
                        "classes": [{"class_id": 130020, "label": "周五晚", "match_prefix": "周五"}],
                    },
                }
            )
            roster = {
                "101": {"学生姓名": "当前学员", "上课时间": "周五晚", "班级": "周五晚"},
                "202": {"学生姓名": "旧名单学员", "上课时间": "周五晚", "班级": "周五晚"},
            }
            payload = {
                "targetWeek": 7,
                "detailRows": [
                    {"userId": "101", "classId": 130020, "lessonSort": 13, "status": "无数据"},
                    {"userId": "101", "classId": 130020, "lessonSort": 14, "status": "无数据"},
                    {"userId": "202", "classId": 130020, "lessonSort": 13, "status": "无数据"},
                    {"userId": "202", "classId": 130020, "lessonSort": 14, "status": "无数据"},
                ],
            }
            with patch.object(server, "WORKSPACE", folder):
                metrics = server.build_completion_metrics(payload, roster, set(), config)["metrics"]
            all_students = next(item for item in metrics if item["id"] == "all")["students"]
            absent = next(item for item in metrics if item["id"] == "absent")["students"]
            self.assertEqual([student["id"] for student in all_students], ["101"])
            self.assertEqual([student["id"] for student in absent], ["101"])


if __name__ == "__main__": unittest.main()
