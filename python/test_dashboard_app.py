"""Exercise real Streamlit widgets without credentials or cloud writes."""
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from dashboard_data import demo_snapshot

APP = Path(__file__).with_name("dashboard_iot.py")


class AppTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        st.cache_resource.clear()
        self.env = patch.dict(os.environ, {"IOT_DEMO": "true", "MONGO_URI": "",
                                         "DASHBOARD_ALLOW_WRITES": "false"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def app(self):
        app = AppTest.from_file(APP, default_timeout=30).run()
        self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
        return app

    def test_demo_tabs_and_preview_do_not_connect(self):
        with patch("pymongo.MongoClient", side_effect=AssertionError("Demo attempted MongoDB connection")):
            app = self.app()
            self.assertEqual(len(app.tabs), 4)
            self.assertTrue(app.button(key="save").disabled)
            self.assertIn("°C", app.metric[0].value)
            app.button(key="preview").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertIn("Tải thống kê lần chạy (JSON)", [b.proto.label for b in app.get("download_button")])

    def test_filter_switches_and_processed_table(self):
        app = self.app()
        app.selectbox(key="window").set_value(60).run()
        app.selectbox(key="minutes").set_value(5).run()
        app.radio(key="table_source").set_value("Dữ liệu đã xử lý").run()
        self.assertEqual(len(app.exception), 0)
        app.multiselect(key="table_columns_Dữ liệu đã xử lý").set_value([]).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Chọn ít nhất" in item.value for item in app.info))

    def test_missing_config_and_recover_to_demo(self):
        with patch.dict(os.environ, {"IOT_DEMO": "false"}):
            app = self.app()
            self.assertEqual(len(app.tabs), 0)
            app.checkbox(key="demo_mode").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)

    def test_live_preview_is_read_only_and_explicit_save_uses_shared_pipeline(self):
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value.distinct.return_value = ["esp32_demo"]
        fixture = demo_snapshot(30, 30)
        with patch.dict(os.environ, {"IOT_DEMO": "false", "MONGO_URI": "mongodb://localhost", "DASHBOARD_ALLOW_WRITES": "true"}), \
                patch("pymongo.MongoClient", return_value=client), \
                patch("dashboard_data.read_snapshot", return_value=fixture), \
                patch("preprocess_iot.save_processed", return_value={"upserted": 17, "modified": 0, "matched": 0}) as save:
            app = self.app()
            app.button(key="preview").click().run()
            save.assert_not_called()
            app.button(key="save").click().run()
            self.assertEqual(len(app.exception), 0)
            save.assert_called_once()
            self.assertTrue(any("17 cửa sổ mới" in s.value for s in app.success))
            client.__getitem__.return_value.__getitem__.return_value.insert_one.assert_not_called()

    def test_truncated_live_data_blocks_processing(self):
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value.distinct.return_value = ["esp32_demo"]
        fixture = demo_snapshot(30, 30)
        fixture["truncated"] = True
        with patch.dict(os.environ, {"IOT_DEMO": "false", "MONGO_URI": "mongodb://localhost", "DASHBOARD_ALLOW_WRITES": "true"}), \
                patch("pymongo.MongoClient", return_value=client), \
                patch("dashboard_data.read_snapshot", return_value=fixture):
            app = self.app()
            self.assertTrue(app.button(key="preview").disabled)
            self.assertTrue(app.button(key="save").disabled)

    def test_atlas_failure_does_not_expose_credentials(self):
        with patch.dict(os.environ, {"IOT_DEMO": "false", "MONGO_URI": "mongodb://private:secret@localhost"}), \
                patch("pymongo.MongoClient", side_effect=RuntimeError("mongodb://private:secret@localhost")):
            app = self.app()
            self.assertTrue(any("RuntimeError" in item.value for item in app.error))
            self.assertFalse(any("secret" in item.value for item in app.error))


if __name__ == "__main__":
    unittest.main()
