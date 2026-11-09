import unittest
from unittest.mock import patch
from source_api import convert_listing, search
from monitor import run_once
import sqlite3, tempfile, pathlib, json
from hunter import DEFAULT_CONFIG

class SourceTests(unittest.TestCase):
    def test_convert_valid(self):
        item=convert_listing({"id":10,"title":"RTX 3070","url":"https://www.avito.ru/a/b","price":12000})
        self.assertEqual(item["external_id"],"10")
    def test_reject_bad_url(self):
        self.assertIsNone(convert_listing({"id":10,"title":"X","url":"https://evil.test","price":1}))
    def test_reject_search_url(self):
        with self.assertRaises(ValueError):search("key","https://evil.test/a")
    def test_budget(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=json.loads(json.dumps(DEFAULT_CONFIG))
            cfg["db_path"]=str(pathlib.Path(d)/"db.sqlite")
            cfg["monitor"]={"enabled":True,"max_calls_per_day":1}
            cfg["profiles"][0]["search_url"]="https://www.avito.ru/moskva?q=rtx+3070"
            with patch("monitor.search",return_value=([],5)) as mocked:
                run_once(cfg,"key")
                run_once(cfg,"key")
                self.assertEqual(mocked.call_count,1)
    def test_disabled_by_default(self):
        self.assertEqual(run_once(DEFAULT_CONFIG,""),{"calls":0,"alerts":0,"details":[],"message":"Автомониторинг выключен"})
if __name__=="__main__":unittest.main()
