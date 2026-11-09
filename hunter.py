"""Market Value Hunter: local-first listing analysis. Python 3.11+, standard library."""
import argparse, hashlib, json, math, os, re, sqlite3, statistics, urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONFIG = {
 "db_path":"hunter.sqlite3","minimum_comparables":5,"minimum_discount":0.2,
 "minimum_profit_rub":2500,"selling_fee_fraction":0.05,"shipping_rub":500,
 "repair_reserve_fraction":0.04,"max_comparable_age_days":60,
 "ai":{"enabled":False,"endpoint":"http://127.0.0.1:11434/api/generate","model":"qwen2.5:7b","max_reviews_per_run":3},
 "telegram":{"enabled":False,"bot_token_env":"TELEGRAM_BOT_TOKEN","chat_id_env":"TELEGRAM_CHAT_ID"},
 "profiles":[{"id":"gpu","keywords":["rtx 3070"],"exclude_keywords":["не работает","на запчасти"],"regions":[],"max_price":25000}]
}
RISK = {"не работает":4,"на запчасти":4,"после майнинга":3,"без проверки":3,"предоплата":4,"не включается":4,"дефект":3,"битый":3,"без гарантии":1}
STOP = {"продам","купить","новый","новая","состояние","оригинал","доставка","торг","бу"}

def now():
 return datetime.now(timezone.utc).isoformat()

def norm(x):
 return re.sub(r"\s+"," ",re.sub(r"[^\w\s]"," ",str(x).lower().replace("ё","е"))).strip()

def tokens(x):
 return set(norm(x).split())-STOP

def init_db(db):
 db.executescript("""
 CREATE TABLE IF NOT EXISTS listings(source TEXT,external_id TEXT,title TEXT,price REAL,description TEXT,region TEXT,url TEXT,first_seen TEXT,last_seen TEXT,profile_id TEXT,PRIMARY KEY(source,external_id,profile_id));
 CREATE TABLE IF NOT EXISTS price_history(source TEXT,external_id TEXT,profile_id TEXT,price REAL,seen_at TEXT);
 CREATE TABLE IF NOT EXISTS alerts(source TEXT,external_id TEXT,profile_id TEXT,created_at TEXT,score REAL,details_json TEXT,PRIMARY KEY(source,external_id,profile_id));
 CREATE TABLE IF NOT EXISTS ai_cache(content_hash TEXT PRIMARY KEY,response TEXT,created_at TEXT);
 """)
 db.commit()

def validate_item(raw):
 for k in ("source","external_id","title","price","url"):
  if k not in raw: raise ValueError("missing: "+k)
 item={k:str(raw[k]).strip() for k in ("source","external_id","title","url")}
 item["price"]=float(raw["price"])
 if not math.isfinite(item["price"]) or item["price"]<=0: raise ValueError("invalid positive price")
 if not all(item.values()): raise ValueError("empty required field")
 if not item["url"].startswith(("http://","https://")): raise ValueError("invalid URL")
 item["description"]=str(raw.get("description",""))[:10000]
 item["region"]=str(raw.get("region",""))[:300]
 return item

def matches_profile(item,p):
 text=norm(item["title"]+" "+item.get("description",""))
 if not all(norm(s) in text for s in p.get("keywords",[])): return False
 if any(norm(s) in text for s in p.get("exclude_keywords",[])): return False
 if p.get("regions") and not any(norm(s) in norm(item["region"]) for s in p["regions"]): return False
 return not p.get("max_price") or item["price"]<=p["max_price"]

def risk_assessment(item):
 text=norm(item["title"]+" "+item.get("description",""))
 flags=[k for k in RISK if k in text]
 return min(10,sum(RISK[k] for k in flags)),flags

def fetch_comps(db,profile_id,item,days):
 rows=db.execute("SELECT title,price,external_id,source FROM listings WHERE profile_id=? AND first_seen >= ?",(profile_id,(datetime.now(timezone.utc).timestamp()-days*86400),)).fetchall() if False else db.execute("SELECT title,price,external_id,source FROM listings WHERE profile_id=?",(profile_id,)).fetchall()
 target=tokens(item["title"])
 nums=set(re.findall(r"\d+[a-zа-я]*",norm(item["title"])))
 out=[]
 for title,price,eid,source in rows:
  if (eid,source)==(item["external_id"],item["source"]): continue
  words=tokens(title)
  if not words or not target or set(re.findall(r"\d+[a-zа-я]*",norm(title)))!=nums: continue
  if len(words&target)/len(words|target)>=0.48: out.append(price)
 return out

def calculate(item,comps,config):
 if len(comps)<config["minimum_comparables"]: return None
 median=statistics.median(comps)
 mad=statistics.median(abs(x-median) for x in comps)
 trimmed=[x for x in comps if abs(x-median)<=max(3*1.4826*mad,median*.2)]
 if len(trimmed)<config["minimum_comparables"]: trimmed=comps
 market=statistics.median(trimmed)
 discount=(market-item["price"])/market
 profit=market-item["price"]-market*(config["selling_fee_fraction"]+config["repair_reserve_fraction"])-config["shipping_rub"]
 risk,flags=risk_assessment(item)
 return {"market_median":round(market),"discount_fraction":round(discount,3),"estimated_profit_rub":round(profit),"risk_score":risk,"risk_flags":flags,"comparable_count":len(trimmed),"score":round(max(0,min(100,discount*180+profit/1000*2-risk*6)),1)}

def ai_review(db,item,metrics,cfg):
 ai=cfg.get("ai",{})
 if not ai.get("enabled"): return None
 endpoint=ai["endpoint"]
 if not re.fullmatch(r"http://(?:localhost|127\.0\.0\.1):\d+/api/generate",endpoint): raise ValueError("Only local Ollama endpoint supported")
 prompt="Проверь несоответствие моделей и признаки дефектов, кратко (не более 70 слов): "+json.dumps({"item":item,"metrics":metrics},ensure_ascii=False)
 key=hashlib.sha256((ai["model"]+prompt).encode()).hexdigest()
 cached=db.execute("SELECT response FROM ai_cache WHERE content_hash=?",(key,)).fetchone()
 if cached:return cached[0]
 payload=json.dumps({"model":ai["model"],"prompt":prompt,"stream":False,"options":{"temperature":0.1,"num_predict":120}}).encode()
 req=urllib.request.Request(endpoint,payload,{"Content-Type":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=45) as response: value=str(json.load(response).get("response",""))[:1000]
 except Exception as exc: return "ИИ недоступен: "+type(exc).__name__
 db.execute("INSERT INTO ai_cache VALUES(?,?,?)",(key,value,now()));db.commit()
 return value

def telegram_send(message,cfg):
 t=cfg.get("telegram",{})
 if not t.get("enabled"):return
 token=os.getenv(t.get("bot_token_env","TELEGRAM_BOT_TOKEN"))
 chat=os.getenv(t.get("chat_id_env","TELEGRAM_CHAT_ID"))
 if not token or not chat: raise ValueError("Set Telegram credentials in environment variables")
 data=json.dumps({"chat_id":chat,"text":message[:4000],"disable_web_page_preview":True}).encode()
 request=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data,{"Content-Type":"application/json"})
 with urllib.request.urlopen(request,timeout=15):pass

def ingest(db,raw_items,cfg):
 results={"imported":0,"skipped":0,"alerts":[]}
 for raw in raw_items:
  item=validate_item(raw)
  for p in cfg["profiles"]:
   if not matches_profile(item,p):continue
   pid=p["id"]
   existing=db.execute("SELECT price FROM listings WHERE source=? AND external_id=? AND profile_id=?",(item["source"],item["external_id"],pid)).fetchone()
   if existing:
    db.execute("UPDATE listings SET last_seen=?,price=?,title=?,description=?,url=? WHERE source=? AND external_id=? AND profile_id=?",(now(),item["price"],item["title"],item["description"],item["url"],item["source"],item["external_id"],pid))
    if existing[0]!=item["price"]:db.execute("INSERT INTO price_history VALUES(?,?,?,?,?)",(item["source"],item["external_id"],pid,item["price"],now()))
    results["skipped"]+=1
    continue
   comps=fetch_comps(db,pid,item,cfg["max_comparable_age_days"])
   metrics=calculate(item,comps,cfg)
   stamp=now()
   db.execute("INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?,?)",(item["source"],item["external_id"],item["title"],item["price"],item["description"],item["region"],item["url"],stamp,stamp,pid))
   db.execute("INSERT INTO price_history VALUES(?,?,?,?,?)",(item["source"],item["external_id"],pid,item["price"],stamp))
   results["imported"]+=1
   if metrics and metrics["discount_fraction"]>=cfg["minimum_discount"] and metrics["estimated_profit_rub"]>=cfg["minimum_profit_rub"] and metrics["risk_score"]<4:
    alert={"profile":pid,"item":item,"metrics":metrics}
    db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?)",(item["source"],item["external_id"],pid,stamp,metrics["score"],json.dumps(alert,ensure_ascii=False)))
    results["alerts"].append(alert)
 db.commit()
 for alert in results["alerts"][:cfg.get("ai",{}).get("max_reviews_per_run",3)]:
  note=ai_review(db,alert["item"],alert["metrics"],cfg)
  if note:alert["ai_review"]=note
 for alert in results["alerts"]:
  m=alert["metrics"];item=alert["item"]
  telegram_send(f'{item["title"]}: {item["price"]:.0f} ₽; медиана {m["market_median"]} ₽; потенциальная прибыль {m["estimated_profit_rub"]} ₽. {item["url"]}',cfg)
 return results

def main():
 parser=argparse.ArgumentParser(description="Local-first marketplace opportunity analysis")
 parser.add_argument("command",choices=["init","ingest","report"])
 parser.add_argument("file",nargs="?")
 parser.add_argument("--config",default="config.json")
 args=parser.parse_args()
 path=Path(args.config)
 if args.command=="init":
  if not path.exists():path.write_text(json.dumps(DEFAULT_CONFIG,ensure_ascii=False,indent=2),encoding="utf-8")
  cfg=json.loads(path.read_text(encoding="utf-8"))
 else:
  if not path.exists():parser.error("Run init first")
  cfg=json.loads(path.read_text(encoding="utf-8"))
 with sqlite3.connect(cfg["db_path"]) as db:
  init_db(db)
  if args.command=="init":print("Database initialized:",cfg["db_path"])
  elif args.command=="ingest":
   if not args.file:parser.error("ingest requires JSON filename")
   items=json.loads(Path(args.file).read_text(encoding="utf-8"))
   if not isinstance(items,list):parser.error("JSON must contain an array")
   print(json.dumps(ingest(db,items,cfg),ensure_ascii=False,indent=2))
  elif args.command=="report":
   rows=db.execute("SELECT details_json FROM alerts ORDER BY score DESC LIMIT 30").fetchall()
   print(json.dumps([json.loads(r[0]) for r in rows],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
