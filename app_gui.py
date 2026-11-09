from contextlib import closing
"""Market Value Hunter graphical desktop application (Windows/Linux)."""
import copy
import json
import os
import sqlite3
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from hunter import DEFAULT_CONFIG, init_db, ingest
from source_mail import DEFAULT_MAIL, init_mail_db, read_mail, validate_settings

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MarketValueHunter"
CONFIG = APP_DIR / "config.json"
SERVICE = "MarketValueHunter"
def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        value = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        value = copy.deepcopy(DEFAULT_CONFIG)
    value["db_path"] = str(APP_DIR / "hunter.sqlite3")
    return value

def split_terms(value):
    return [x.strip() for x in value.replace("\n", ",").split(",") if x.strip()]

class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Market Value Hunter — поиск выгодных товаров")
        self.geometry("1050x710")
        self.minsize(800, 580)
        self.cfg = load_config()
        self.busy = False
        self.monitor_running = False
        self.stop_monitor = threading.Event()
        self.mail_timer = None
        self.create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.reload_data()
        self.schedule_next()

    def create_widgets(self):
        style=ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        top=ttk.Frame(self,padding=12)
        top.pack(fill="x")
        ttk.Label(top,text="Market Value Hunter",font=("Segoe UI",18,"bold")).pack(side="left")
        ttk.Label(top,text="Бесплатные уведомления Авито из вашей почты",foreground="#666").pack(side="right")
        self.tabs=ttk.Notebook(self)
        self.tabs.pack(fill="both",expand=True,padx=12,pady=6)
        self.results=ttk.Frame(self.tabs,padding=12)
        self.searches=ttk.Frame(self.tabs,padding=12)
        self.mail_tab=ttk.Frame(self.tabs,padding=12)
        self.settings=ttk.Frame(self.tabs,padding=12)
        self.tabs.add(self.mail_tab,text="Новые из почты")
        self.tabs.add(self.results,text="Оценка выгоды (JSON)")
        self.tabs.add(self.searches,text="Поиски")
        self.tabs.add(self.settings,text="Настройки")
        self.build_mail()
        self.build_results()
        self.build_searches()
        self.build_settings()
        self.status=tk.StringVar(value="Настройте почту, проверьте подключение и нажмите «Проверить почту сейчас».")
        ttk.Label(self,textvariable=self.status,anchor="w",padding=10).pack(fill="x")

    def build_results(self):
        bar=ttk.Frame(self.results)
        bar.pack(fill="x",pady=(0,8))
        self.import_button=ttk.Button(bar,text="Загрузить объявления (JSON)",command=self.import_json)
        self.import_button.pack(side="left",padx=(0,8))
        ttk.Button(bar,text="Загрузить демо",command=self.import_demo).pack(side="left",padx=(0,8))
        ttk.Button(bar,text="Обновить",command=self.reload_data).pack(side="left",padx=(0,8))
        ttk.Button(bar,text="Проверить почту сейчас",command=self.monitor_now).pack(side="left")

        ttk.Button(bar,text="Открыть объявление",command=self.open_selected).pack(side="right")
        cols=("score","price","market","profit","title","profile","url")
        self.table=ttk.Treeview(self.results,columns=cols,show="headings",selectmode="browse")
        labels={"score":"Оценка","price":"Цена ₽","market":"Рынок ₽","profit":"Прибыль ₽","title":"Объявление","profile":"Поиск","url":"Ссылка"}
        widths={"score":75,"price":100,"market":105,"profit":110,"title":330,"profile":100,"url":240}
        for name in cols:
            self.table.heading(name,text=labels[name],command=lambda col=name:self.sort_by(col,False))
            self.table.column(name,width=widths[name],anchor="e" if name in ("score","price","market","profit") else "w")
        scroll=ttk.Scrollbar(self.results,orient="vertical",command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        self.table.bind("<Double-1>",lambda event:self.open_selected())

    def build_searches(self):
        frame=ttk.Frame(self.searches)
        frame.pack(fill="both",expand=True)
        left=ttk.Frame(frame)
        left.pack(side="left",fill="y",padx=(0,15))
        self.profile_list=tk.Listbox(left,width=26,exportselection=False)
        self.profile_list.pack(fill="y",expand=True)
        self.profile_list.bind("<<ListboxSelect>>",self.select_profile)
        ttk.Button(left,text="Добавить поиск",command=self.add_profile).pack(fill="x",pady=(10,4))
        ttk.Button(left,text="Удалить поиск",command=self.remove_profile).pack(fill="x")
        edit=ttk.Frame(frame)
        edit.pack(side="left",fill="both",expand=True)
        fields=[("id","Название/ID поиска"),("keywords","Ключевые слова (через запятую)"),("exclude_keywords","Исключить слова"),("regions","Регионы (пусто = все)"),("max_price","Максимальная цена, ₽"),("search_url","Ссылка поиска Авито (открыть и сохранить на сайте)")]
        self.profile_inputs={}
        for index,(key,label) in enumerate(fields):
            ttk.Label(edit,text=label).grid(row=index*2,column=0,sticky="w",pady=(6,2))
            inp=ttk.Entry(edit,width=65)
            inp.grid(row=index*2+1,column=0,sticky="ew",pady=(0,6))
            self.profile_inputs[key]=inp
        edit.columnconfigure(0,weight=1)
        ttk.Label(edit,text="Все ключевые слова должны присутствовать в объявлении.\nДля поиска RTX 3070 укажите: rtx 3070",foreground="#555").grid(row=12,column=0,sticky="w",pady=12)
        ttk.Button(edit,text="Сохранить поиск",command=self.save_profile).grid(row=13,column=0,sticky="w")
        ttk.Button(edit,text="Открыть поиск на Авито",command=self.open_search).grid(row=14,column=0,sticky="w",pady=8)
        self.refresh_profiles()

    def build_settings(self):
        canvas=tk.Canvas(self.settings,highlightthickness=0)
        scrollbar=ttk.Scrollbar(self.settings,orient="vertical",command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left",fill="both",expand=True)
        scrollbar.pack(side="right",fill="y")
        interior=ttk.Frame(canvas,padding=(8,0,20,20))
        window=canvas.create_window((0,0),window=interior,anchor="nw")
        interior.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(window,width=e.width-4))
        self.setting_vars={}
        fields=[
            ("minimum_comparables","Минимум похожих объявлений","int"),
            ("minimum_discount","Минимальная скидка (например 0.20 = 20%)","float"),
            ("minimum_profit_rub","Минимальная ожидаемая прибыль, ₽","int"),
            ("selling_fee_fraction","Комиссии при продаже (0.05 = 5%)","float"),
            ("shipping_rub","Доставка и расходы, ₽","int"),
            ("repair_reserve_fraction","Резерв на неисправности (0.04 = 4%)","float"),
        ]
        for i,(key,label,kind) in enumerate(fields):
            ttk.Label(interior,text=label).grid(row=i,column=0,sticky="w",pady=5)
            var=tk.StringVar(value=str(self.cfg.get(key,"")))
            ttk.Entry(interior,textvariable=var,width=24).grid(row=i,column=1,sticky="w",padx=12)
            self.setting_vars[key]=(var,kind)
        row=len(fields)
        ttk.Separator(interior).grid(row=row,column=0,columnspan=3,sticky="ew",pady=14)
        row+=1
        self.telegram_enabled=tk.BooleanVar(value=self.cfg.get("telegram",{}).get("enabled",False))
        ttk.Checkbutton(interior,text="Telegram: отправлять новые сигналы",variable=self.telegram_enabled).grid(row=row,column=0,columnspan=3,sticky="w")
        row+=1
        ttk.Label(interior,text="Токен Telegram-бота").grid(row=row,column=0,sticky="w",pady=5)
        self.bot_token=tk.StringVar()
        ttk.Entry(interior,textvariable=self.bot_token,width=48,show="•").grid(row=row,column=1,sticky="w",padx=12)
        row+=1
        ttk.Label(interior,text="Chat ID").grid(row=row,column=0,sticky="w",pady=5)
        self.chat_id=tk.StringVar()
        ttk.Entry(interior,textvariable=self.chat_id,width=32).grid(row=row,column=1,sticky="w",padx=12)
        row+=1
        ttk.Label(interior,text="Токен хранится в системном хранилище учётных данных, а не в config.json.",foreground="#555").grid(row=row,column=0,columnspan=3,sticky="w",pady=(0,12))
        row+=1
        ttk.Separator(interior).grid(row=row,column=0,columnspan=3,sticky="ew",pady=10)
        row+=1
        self.ai_enabled=tk.BooleanVar(value=self.cfg.get("ai",{}).get("enabled",False))
        ttk.Checkbutton(interior,text="Локальный ИИ (требуется установленный Ollama)",variable=self.ai_enabled).grid(row=row,column=0,columnspan=3,sticky="w")
        row+=1
        ttk.Label(interior,text="Модель Ollama").grid(row=row,column=0,sticky="w",pady=5)
        self.ai_model=tk.StringVar(value=self.cfg.get("ai",{}).get("model","qwen2.5:7b"))
        ttk.Entry(interior,textvariable=self.ai_model,width=28).grid(row=row,column=1,sticky="w",padx=12)
        row+=1
        ttk.Label(interior,text="Максимум ИИ-проверок за импорт").grid(row=row,column=0,sticky="w",pady=5)
        self.ai_budget=tk.StringVar(value=str(self.cfg.get("ai",{}).get("max_reviews_per_run",3)))
        ttk.Entry(interior,textvariable=self.ai_budget,width=8).grid(row=row,column=1,sticky="w",padx=12)
        row+=1
        ttk.Separator(interior).grid(row=row,column=0,columnspan=3,sticky="ew",pady=10)
        row+=1
        ttk.Label(interior,text="Бесплатный источник настраивается во вкладке «Новые из почты».\nИИ выключен по умолчанию; чтение писем не использует токены.").grid(row=row,column=0,columnspan=3,sticky="w")
        self.load_secrets()
        row+=1
        ttk.Button(interior,text="Сохранить все настройки",command=self.save_settings).grid(row=row,column=0,sticky="w",pady=20)

    def load_secrets(self):
        if "--smoke-test" in sys.argv:return
        try:
            import keyring
            self.bot_token.set(keyring.get_password(SERVICE,"telegram_token") or "")
            self.chat_id.set(keyring.get_password(SERVICE,"telegram_chat_id") or "")
            self.mail_password.set(keyring.get_password(SERVICE,"imap_password") or "")
        except Exception:
            pass

    def refresh_profiles(self,selected=0):
        self.profile_list.delete(0,tk.END)
        for p in self.cfg.get("profiles",[]):self.profile_list.insert(tk.END,p.get("id","?"))
        if self.cfg.get("profiles"):
            index=min(selected,len(self.cfg["profiles"])-1)
            self.profile_list.selection_set(index)
            self.select_profile()

    def selected_index(self):
        selection=self.profile_list.curselection()
        return selection[0] if selection else None

    def select_profile(self,event=None):
        index=self.selected_index()
        if index is None:return
        p=self.cfg["profiles"][index]
        for key,entry in self.profile_inputs.items():
            entry.delete(0,tk.END)
            value=p.get(key,"")
            entry.insert(0,", ".join(value) if isinstance(value,list) else str(value or ""))

    def add_profile(self):
        ids={p["id"] for p in self.cfg["profiles"]}
        n=1
        while "search"+str(n) in ids:n+=1
        self.cfg["profiles"].append({"id":"search"+str(n),"keywords":[],"exclude_keywords":[],"regions":[],"max_price":0,"search_url":""})
        self.persist()
        self.refresh_profiles(len(self.cfg["profiles"])-1)

    def remove_profile(self):
        index=self.selected_index()
        if index is None:return
        if not messagebox.askyesno("Удаление","Удалить выбранный профиль поиска?"):return
        self.cfg["profiles"].pop(index)
        self.persist()
        self.refresh_profiles()

    def save_profile(self):
        index=self.selected_index()
        if index is None:return
        try:
            values={key:entry.get().strip() for key,entry in self.profile_inputs.items()}
            pid=values["id"]
            if not pid or any(p["id"]==pid for i,p in enumerate(self.cfg["profiles"]) if i!=index):raise ValueError("Название поиска должно быть уникальным.")
            max_price=float(values["max_price"] or 0)
            if max_price<0:raise ValueError("Цена не может быть отрицательной.")
            search_url=values["search_url"]
            if search_url:
                from urllib.parse import urlsplit
                uri=urlsplit(search_url)
                if uri.scheme!="https" or uri.hostname not in ("www.avito.ru","avito.ru"):
                    raise ValueError("Ссылка должна вести на https://www.avito.ru")
            self.cfg["profiles"][index]={"id":pid,"keywords":split_terms(values["keywords"]),"exclude_keywords":split_terms(values["exclude_keywords"]),"regions":split_terms(values["regions"]),"max_price":max_price,"search_url":search_url}
            self.persist()
            self.refresh_profiles(index)
            messagebox.showinfo("Сохранено","Параметры поиска сохранены.")
        except ValueError as exc:messagebox.showerror("Проверьте данные",str(exc))

    def open_search(self):
        from urllib.parse import urlsplit
        url=self.profile_inputs["search_url"].get().strip()
        try:
            uri=urlsplit(url)
            if uri.scheme!="https" or uri.hostname not in ("avito.ru","www.avito.ru") or uri.username or uri.password:
                raise ValueError("Сначала укажите HTTPS-ссылку поиска Авито.")
            webbrowser.open(url)
        except ValueError as exc:messagebox.showerror("Поиск",str(exc))

    def save_settings(self):
        try:
            for key,(var,kind) in self.setting_vars.items():
                value=int(var.get()) if kind=="int" else float(var.get())
                if not (0<=value<=100000000):raise ValueError("Недопустимое значение: "+key)
                self.cfg[key]=value
            if self.cfg["minimum_comparables"]<2:raise ValueError("Нужно минимум 2 сравнимых объявления.")
            for key in ("minimum_discount","selling_fee_fraction","repair_reserve_fraction"):
                if self.cfg[key]>1:raise ValueError(key+" должно быть от 0 до 1.")
            self.cfg.setdefault("telegram",{})["enabled"]=bool(self.telegram_enabled.get())
            self.cfg.setdefault("ai",{})["enabled"]=bool(self.ai_enabled.get())
            self.cfg["ai"]["model"]=self.ai_model.get().strip()
            budget=int(self.ai_budget.get())
            if not 0<=budget<=100:raise ValueError("Лимит ИИ должен быть в диапазоне 0–100.")
            self.cfg["ai"]["max_reviews_per_run"]=budget
            if self.telegram_enabled.get() and (not self.bot_token.get().strip() or not self.chat_id.get().strip()):
                raise ValueError("Для Telegram укажите токен бота и Chat ID.")
            import keyring
            for username,value in (("telegram_token",self.bot_token.get().strip()),("telegram_chat_id",self.chat_id.get().strip())):
                if value:keyring.set_password(SERVICE,username,value)
                else:
                    try:keyring.delete_password(SERVICE,username)
                    except keyring.errors.PasswordDeleteError:pass
            self.persist()
            messagebox.showinfo("Сохранено","Настройки сохранены.")
        except Exception as exc:messagebox.showerror("Ошибка настроек",str(exc))

    def persist(self):
        APP_DIR.mkdir(parents=True,exist_ok=True)
        tmp=CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cfg,ensure_ascii=False,indent=2),encoding="utf-8")
        tmp.replace(CONFIG)

    def import_demo(self):
        location=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent)) / "example_listings.json"
        if not location.exists():
            messagebox.showerror("Не найдено","Демонстрационные данные отсутствуют.")
            return
        self.load_file(location)

    def import_json(self):
        filename=filedialog.askopenfilename(title="Выберите файл с объявлениями",filetypes=[("JSON","*.json"),("Все файлы","*.*")])
        if filename:self.load_file(Path(filename))

    def load_file(self,path):
        if self.busy:return
        self.busy=True
        self.import_button.configure(state="disabled")
        self.status.set("Обрабатываются объявления…")
        cfg=copy.deepcopy(self.cfg)
        def worker():
            try:
                raw=json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(raw,list):raise ValueError("Файл должен содержать JSON-массив объявлений.")
                import keyring
                token=keyring.get_password(SERVICE,"telegram_token")
                chat=keyring.get_password(SERVICE,"telegram_chat_id")
                if token:os.environ[cfg["telegram"].get("bot_token_env","TELEGRAM_BOT_TOKEN")]=token
                if chat:os.environ[cfg["telegram"].get("chat_id_env","TELEGRAM_CHAT_ID")]=chat
                with closing(sqlite3.connect(cfg["db_path"])) as db:
                    init_db(db)
                    result=ingest(db,raw,cfg)
                self.after(0,lambda:self.complete_import(result,None))
            except Exception as exc:
                self.after(0,lambda message=str(exc):self.complete_import(None,message))
        threading.Thread(target=worker,daemon=True).start()

    def complete_import(self,result,error):
        self.busy=False
        self.import_button.configure(state="normal")
        if error:
            self.status.set("Ошибка импорта")
            messagebox.showerror("Ошибка",error)
            return
        self.reload_data()
        count=len(result.get("alerts",[]))
        self.status.set(f"Импортировано: {result.get('imported',result.get('new',0))}; новых сигналов: {count}.")
        messagebox.showinfo("Импорт завершён",self.status.get())

    def reload_data(self):
        for iid in self.table.get_children():self.table.delete(iid)
        APP_DIR.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.cfg["db_path"])) as db:
            init_db(db)
            rows=db.execute("SELECT details_json FROM alerts ORDER BY score DESC LIMIT 500").fetchall()
        for (payload,) in rows:
            alert=json.loads(payload)
            m=alert["metrics"];item=alert["item"]
            self.table.insert("",tk.END,values=(m["score"],round(item["price"]),m["market_median"],m["estimated_profit_rub"],item["title"],alert["profile"],item["url"]))
        self.refresh_mail()
        self.status.set(f"Сохранено выгодных предложений: {len(rows)}. Оценка выгоды — только по данным JSON; письма показаны отдельно.")

    def sort_by(self,col,descending):
        items=[(self.table.set(iid,col),iid) for iid in self.table.get_children("")]
        def key(pair):
            if col in ("score","price","market","profit"):
                try:return float(pair[0])
                except ValueError:return -1
            return pair[0].lower()
        items.sort(key=key,reverse=descending)
        for index,(_,iid) in enumerate(items):self.table.move(iid,"",index)
        self.table.heading(col,command=lambda:self.sort_by(col,not descending))


    def build_mail(self):
        intro=("1. На Авито откройте нужный поиск, сохраните его и включите письма о новых объявлениях, если эта опция доступна.\n"
               "2. Убедитесь, что письмо действительно пришло. Разрешите IMAP в почте и создайте пароль приложения.\n"
               "3. Выберите почту, введите адрес и пароль приложения, проверьте подключение и сохраните настройки.\n"
               "Без оплаты API.SH, подписки программы и ИИ. Окно должно оставаться открытым; при закрытии проверки остановятся.\n"
               "Доступность, задержку и полноту писем определяет Авито. Если email-рассылки нет — этот режим не получает объявления.")
        ttk.Label(self.mail_tab,text=intro,wraplength=950).pack(anchor="w",pady=(0,8))
        form=ttk.Frame(self.mail_tab)
        form.pack(fill="x")
        settings=dict(DEFAULT_MAIL,**self.cfg.get("mail",{}))
        self.mail_enabled=tk.BooleanVar(value=settings["enabled"])
        ttk.Checkbutton(form,text="Проверять почту автоматически",variable=self.mail_enabled).grid(row=0,column=0,columnspan=4,sticky="w")
        provider=tk.StringVar(value="Выберите почту")
        combo=ttk.Combobox(form,textvariable=provider,values=["Яндекс", "Mail.ru", "Другая (IMAP)"],state="readonly",width=22)
        combo.grid(row=1,column=0,sticky="w",pady=4)
        self.mail_vars={}
        fields=[("username","Ваш адрес почты"),("host","IMAP-сервер"),("port","Порт TLS"),("folder","Папка (INBOX или Avito)"),("interval_minutes","Интервал, минут (1–1440)"),("lookback_days","Первый импорт, дней (1–30)")]
        for i,(key,label) in enumerate(fields):
            r=2+i//2; c=(i%2)*2
            ttk.Label(form,text=label).grid(row=r,column=c,sticky="w",padx=(0,8),pady=3)
            var=tk.StringVar(value=str(settings[key]))
            ttk.Entry(form,textvariable=var,width=26).grid(row=r,column=c+1,sticky="ew",padx=(0,12))
            self.mail_vars[key]=var
        combo.bind("<<ComboboxSelected>>",lambda e:self.mail_vars["host"].set({"Яндекс":"imap.yandex.ru","Mail.ru":"imap.mail.ru"}.get(provider.get(),self.mail_vars["host"].get())))
        self.mail_password=tk.StringVar()
        ttk.Label(form,text="Пароль приложения").grid(row=5,column=0,sticky="w")
        ttk.Entry(form,textvariable=self.mail_password,show="•",width=26).grid(row=5,column=1,sticky="w")
        buttons=ttk.Frame(self.mail_tab)
        buttons.pack(fill="x",pady=8)
        ttk.Button(buttons,text="Проверить подключение",command=lambda:self.monitor_now(check_only=True)).pack(side="left",padx=4)
        ttk.Button(buttons,text="Сохранить настройки почты",command=self.save_mail_settings).pack(side="left",padx=4)
        ttk.Button(buttons,text="Проверить почту сейчас",command=self.monitor_now).pack(side="left",padx=4)
        ttk.Button(buttons,text="Открыть объявление",command=self.open_mail_selected).pack(side="right")
        ttk.Label(self.mail_tab,text="Прямые ссылки из писем; цены, регионы и прибыль не угадываются. Фильтры задавайте в сохранённом поиске Авито.\n"
                  "Отправитель From может быть подделан: проверяйте письмо. Ссылки-переадресации и вложения не обрабатываются.",wraplength=950).pack(anchor="w",pady=(0,8))
        self.mail_table=ttk.Treeview(self.mail_tab,columns=("date","title","subject","url"),show="headings")
        for key,label,width in (("date","Получено программой (UTC)",180),("title","Текст ссылки / тема письма",280),("subject","Тема",220),("url","Ссылка",220)):
            self.mail_table.heading(key,text=label)
            self.mail_table.column(key,width=width)
        scroll=ttk.Scrollbar(self.mail_tab,orient="vertical",command=self.mail_table.yview)
        self.mail_table.configure(yscrollcommand=scroll.set)
        self.mail_table.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        self.mail_table.bind("<Double-1>",lambda e:self.open_mail_selected())

    def mail_form(self):
        return validate_settings(dict({k:v.get() for k,v in self.mail_vars.items()},enabled=self.mail_enabled.get()))

    def save_mail_settings(self):
        try:
            settings=self.mail_form()
            password=self.mail_password.get()
            if settings["enabled"] and not password:
                raise ValueError("Укажите пароль приложения почты.")
            import keyring
            if password:keyring.set_password(SERVICE,"imap_password",password)
            else:
                try:keyring.delete_password(SERVICE,"imap_password")
                except keyring.errors.PasswordDeleteError:pass
            self.cfg["mail"]=settings
            # Old paid configuration cannot cause a request in the free GUI.
            self.cfg.setdefault("monitor",{})["enabled"]=False
            self.persist()
            self.schedule_next()
            messagebox.showinfo("Сохранено","Настройки почты сохранены. Пароль — в системном хранилище Windows.")
        except Exception as exc:messagebox.showerror("Настройки почты",str(exc))

    def refresh_mail(self):
        with closing(sqlite3.connect(self.cfg["db_path"],timeout=30)) as db:
            init_mail_db(db)
            rows=db.execute("SELECT received_at,title,subject,url FROM mail_items ORDER BY received_at DESC,external_id DESC LIMIT 1000").fetchall()
        self.mail_table.delete(*self.mail_table.get_children())
        for row in rows:self.mail_table.insert("",tk.END,values=row)

    def open_mail_selected(self):
        from source_mail import canonical_link
        selection=self.mail_table.selection()
        if selection:
            link=canonical_link(self.mail_table.set(selection[0],"url"))
            if link:webbrowser.open(link[1])

    def schedule_next(self):
        if self.mail_timer is not None:self.after_cancel(self.mail_timer)
        self.mail_timer=None
        if self.stop_monitor.is_set() or not self.cfg.get("mail",{}).get("enabled"):return
        interval=max(1,int(self.cfg["mail"].get("interval_minutes",5)))
        self.mail_timer=self.after(interval*60000,self.background_monitor)

    def background_monitor(self):
        self.mail_timer=None
        if self.stop_monitor.is_set():return
        self.monitor_now(silent=True)
        self.schedule_next()

    def monitor_now(self,silent=False,check_only=False):
        if self.monitor_running:return
        try:
            if silent:
                settings=validate_settings(self.cfg.get("mail",{}))
                import keyring
                password=keyring.get_password(SERVICE,"imap_password")
            else:
                settings=self.mail_form()
                password=self.mail_password.get()
            if not password:raise ValueError("Укажите пароль приложения почты.")
        except Exception as exc:
            if not silent:messagebox.showerror("Почта",str(exc))
            else:self.status.set("Почта: проверьте сохранённые настройки и пароль приложения.")
            return
        self.monitor_running=True
        self.status.set("Безопасное чтение почты…")
        def worker():
            try:
                result=read_mail(settings,password,self.cfg["db_path"],check_only=check_only)
                if not self.stop_monitor.is_set():self.after(0,lambda:self.monitor_done(result,None,silent))
            except Exception as exc:
                if not self.stop_monitor.is_set():self.after(0,lambda msg=str(exc):self.monitor_done(None,msg,silent))
        threading.Thread(target=worker,daemon=True).start()

    def monitor_done(self,result,error,silent):
        self.monitor_running=False
        if error:
            self.status.set("Ошибка почты: "+error)
            if not silent:messagebox.showerror("Почта",error)
            return
        self.refresh_mail()
        text=result.get("message") or (f"Писем проверено: {result['processed']}; новых ссылок: {result['new']}; "
            f"без прямых ссылок: {result['without_links']}; слишком больших: {result['oversized']}; "
            f"в очереди: {result['pending']}. Отсутствие ссылок не означает отсутствие новых объявлений.")
        self.status.set(text)
        if not silent:messagebox.showinfo("Почта",text)
    def close_app(self):
        self.stop_monitor.set()
        self.destroy()

    def open_selected(self):
        selection=self.table.selection()
        if not selection:
            messagebox.showinfo("Выберите объявление","Выделите строку в таблице.")
            return
        url=self.table.set(selection[0],"url")
        if url.startswith(("https://","http://")):webbrowser.open(url)

def smoke_test(output):
    """Exercise the packaged GUI and mail parser in isolation, without credentials/network."""
    import tempfile
    from email.message import EmailMessage
    from source_mail import parse_message
    global APP_DIR, CONFIG
    with tempfile.TemporaryDirectory() as directory:
        APP_DIR=Path(directory)
        CONFIG=APP_DIR / "config.json"
        app=Application()
        app.withdraw()
        try:
            assert app.tabs.index("end")==4
            msg=EmailMessage()
            msg["From"]="notify@avito.ru"
            msg.set_content("https://www.avito.ru/moskva/gpu_123456")
            item=parse_message(msg.as_bytes())[0]
            with closing(sqlite3.connect(app.cfg["db_path"])) as db:
                db.execute("INSERT INTO mail_items VALUES(?,?,?,?,?,?)",
                           (item["external_id"],item["title"],item["url"],"Smoke","","2026-01-01"))
                db.commit()
            app.refresh_mail()
            assert len(app.mail_table.get_children())==1
            app.update()
            Path(output).write_text(json.dumps({"gui":"ok","mail":"ok","network_calls":0}),encoding="utf-8")
        finally:
            app.close_app()


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--smoke-test":smoke_test(sys.argv[2])
    else:Application().mainloop()
