import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("sync_makeup_sheet_test", ROOT / "scripts/sync_makeup_sheet.py")
makeup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = makeup
spec.loader.exec_module(makeup)


class SyncMakeupSheetTests(unittest.TestCase):
    def test_crm_finished_status_excludes_stale_learning_sheet_row(self):
        values = [
            ["用户id", "学生姓名", "上课时间", "W7到课/完课情况"],
            ["101", "测试学员", "周五晚", "未到课"],
            ["202", "待补课学员", "周五晚", "未到课"],
        ]
        rows = makeup.build_rows(
            values,
            makeup_times={},
            phone_followups={},
            replies={},
            leave_reasons={},
            week=7,
            status_overrides={"101": "已完课", "202": "未到课"},
        )
        self.assertEqual([row[0] for row in rows], ["202"])

    def test_completion_status_overrides_uses_latest_crm_lessons(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "completion.json"
            path.write_text(
                json.dumps(
                    {
                        "detailRows": [
                            {"userId": "101", "classId": 1, "lessonSort": 13, "status": "已完课"},
                            {"userId": "101", "classId": 1, "lessonSort": 14, "status": "已完课"},
                            {"userId": "202", "classId": 1, "lessonSort": 13, "status": "已完课"},
                            {"userId": "202", "classId": 1, "lessonSort": 14, "status": "无数据"},
                            {"userId": "303", "classId": 1, "lessonSort": 13, "status": "已完课"},
                            {"userId": "303", "classId": 1, "lessonSort": 14, "status": "已完课"},
                            {"userId": "303", "classId": 2, "lessonSort": 13, "status": "无数据"},
                            {"userId": "303", "classId": 2, "lessonSort": 14, "status": "无数据"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(makeup, "context_for") as context_for:
                context_for.return_value.first_course = 13
                context_for.return_value.second_course = 14
                statuses = makeup.completion_status_overrides(path, 7)
        self.assertEqual(statuses["101"], "已完课")
        self.assertEqual(statuses["202"], "到课未完课")
        self.assertEqual(statuses["303"], "已完课")


if __name__ == "__main__":
    unittest.main()
