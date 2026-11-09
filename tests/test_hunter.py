import json,sqlite3,unittest
from hunter import DEFAULT_CONFIG,init_db,ingest,calculate,matches_profile,validate_item,fetch_comps
class HunterTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(":memory:");init_db(self.db)
  self.cfg=json.loads(json.dumps(DEFAULT_CONFIG))
  self.cfg["minimum_comparables"]=3;self.cfg["minimum_profit_rub"]=1000;self.cfg["profiles"][0]["max_price"]=40000
 def tearDown(self):self.db.close()
 def item(self,n,price,title="RTX 3070 8GB видеокарта"):
  return {"source":"import","external_id":str(n),"title":title,"price":price,"description":"Работает","region":"Москва","url":f"https://example.org/{n}"}
 def test_alert_dedup(self):
  for n,p in enumerate([21000,22000,20000,23000],1):ingest(self.db,[self.item(n,p)],self.cfg)
  self.assertEqual(len(ingest(self.db,[self.item(5,13000)],self.cfg)["alerts"]),1)
  self.assertEqual(len(ingest(self.db,[self.item(5,13000)],self.cfg)["alerts"]),0)
 def test_model_separation(self):
  ingest(self.db,[self.item(1,21000,"RTX 3060 8GB видеокарта")],self.cfg)
  self.assertEqual(fetch_comps(self.db,"gpu",self.item(2,13000),60),[])
 def test_bad_price(self):
  with self.assertRaises(ValueError):validate_item(self.item(1,-10))
 def test_risk(self):
  self.assertIsNone(calculate(self.item(1,10000),[20000],self.cfg))
  for n,p in enumerate([21000,22000,20000,23000],1):ingest(self.db,[self.item(n,p)],self.cfg)
  bad=self.item(7,9000);bad["description"]="Не работает, предоплата"
  self.assertEqual(len(ingest(self.db,[bad],self.cfg)["alerts"]),0)
 def test_filter(self):
  self.assertTrue(matches_profile(self.item(1,20000),self.cfg["profiles"][0]))
  self.assertFalse(matches_profile(self.item(1,20000,"GTX 1060"),self.cfg["profiles"][0]))
if __name__=="__main__":unittest.main()
