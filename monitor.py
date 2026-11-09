"""Cost-bounded search scheduler for API.SH. Import functions do not spend credits."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hunter import init_db, ingest
from source_api import search

def _ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS api_usage
        (day TEXT NOT NULL, profile_id TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
        credits REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(day,profile_id))""")
    db.commit()

def run_once(cfg, api_key):
    """Runs at most one paid call per profile, within hard daily limit."""
    if not cfg.get("monitor",{}).get("enabled",False):
        return {"calls":0,"alerts":0,"details":[],"message":"Автомониторинг выключен"}
    monitor = cfg["monitor"]
    max_daily = max(0,min(50,int(monitor.get("max_calls_per_day",5))))
    db_path = Path(cfg["db_path"])
    db_path.parent.mkdir(parents=True,exist_ok=True)
    details=[]
    with sqlite3.connect(db_path, timeout=30) as db:
        init_db(db)
        _ensure(db)
        day=datetime.now(timezone.utc).date().isoformat()
        used=db.execute("SELECT COALESCE(SUM(calls),0) FROM api_usage WHERE day=?",(day,)).fetchone()[0]
        for profile in cfg.get("profiles",[]):
            url=profile.get("search_url","").strip()
            if not url or used>=max_daily:continue
            # Reserve the request BEFORE network call so failures cannot trigger repeated paid retries.
            db.execute("""INSERT INTO api_usage(day,profile_id,calls,credits) VALUES(?,?,1,0)
                ON CONFLICT(day,profile_id) DO UPDATE SET calls=calls+1""",(day,profile["id"]))
            db.commit()
            used+=1
            try:
                items,credits=search(api_key,url)
                db.execute("UPDATE api_usage SET credits=credits+? WHERE day=? AND profile_id=?",
                           (credits,day,profile["id"]))
                # Process each search only against its own profile.
                scoped=dict(cfg)
                scoped["profiles"]=[profile]
                result=ingest(db,items,scoped)
                details.append({"profile":profile["id"],"fetched":len(items),
                    "new":result.get("imported",0),"alerts":len(result.get("alerts",[])),
                    "credits":credits})
            except Exception as exc:
                details.append({"profile":profile["id"],"error":str(exc)})
            db.commit()
        return {"calls":used,"alerts":sum(x.get("alerts",0) for x in details),"details":details,
                "message":"Лимит на сегодня исчерпан" if used>=max_daily else "Проверка завершена"}
