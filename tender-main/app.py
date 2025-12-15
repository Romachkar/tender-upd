import os
import sys
import logging
from datetime import datetime
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import json
import generate_report
import json
from search_services import SearchService
import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'mindsearch')))

import generate_report
import customtkinter as ctk

from search_services import SearchService  # Импортируйте SearchService
import os
import logging

from dotenv import load_dotenv   # <- обязательно

# === ВАЖНО: грузим .env ДО всех локальных импортов ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)

# (опционально, чтобы убедиться)
if not os.getenv("OPENROUTER_API_KEY"):
    print("!!! OPENROUTER_API_KEY не найден в .env")
else:
    print("OPENROUTER_API_KEY загружен")
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print(
        "Модуль python-dotenv не установлен. "
        "Переменные окружения будут взяты из системы."
    )

# --- импорт локальных модулей ---

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import read_services
    import ai_services
    import generate_report
except ImportError as e:
    print(f"Ошибка импорта модулей: {e}")
    import types
    read_services = types.ModuleType("read_services")
    ai_services = types.ModuleType("ai_services")
    generate_report = types.ModuleType("generate_report")


class TenderAnalyzerApp(ctk.CTk):
    """
    Главное окно Windows-приложения анализа тендеров.
    """

    def __init__(self):
        super().__init__()

        # Инициализация поискового сервиса
        self.search_service = SearchService()


        # состояние
        self.current_files: list[str] = []
        self.analysis_in_progress: bool = False
        self.analyzed_data: str | None = None
        self.aggregated_json_path: str | None = None

        # логирование
        self._setup_logging()

        # города
        self.cities_list = self._load_cities_from_file()

        # UI
        self._setup_ui()

        self.logger.info("Tender Analyzer запущен")

    # ------------------------------------------------------------------ #
    #   СЕРВИСНЫЕ МЕТОДЫ
    # ------------------------------------------------------------------ #

    def _load_cities_from_file(self) -> list[str]:
        """
        Загружает список городов из cities.txt (через запятую).
        Если файла нет или ошибка — возвращает базовый список.
        """
        try:
            cities_file_path = os.path.join(
                os.path.dirname(__file__), "cities.txt"
            )
            if os.path.exists(cities_file_path):
                with open(cities_file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                cities = [c.strip() for c in content.split(",") if c.strip()]
                cities = sorted(list(set(cities)))
                print(f"Загружено {len(cities)} городов из cities.txt")
                return cities
            else:
                print(
                    "Файл cities.txt не найден, используется базовый список городов"
                )
        except Exception as e:
            print(f"Ошибка при загрузке городов: {e}")
            print("Используется базовый список городов")

        return [
            "Москва",
            "Санкт-Петербург",
            "Новосибирск",
            "Екатеринбург",
            "Казань",
            "Нижний Новгород",
        ]

    def _setup_logging(self):
        """
        Логирование в файл + консоль + GUI.
        """
        self.logger = logging.getLogger("TenderAnalyzer")
        self.logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # файл
        try:
            log_file = os.path.join(
                os.path.dirname(__file__), "tender_analyzer.log"
            )
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)
        except Exception as e:
            print(f"Не удалось создать файл логов: {e}")

        # консоль
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)

        # обработчик для GUI – добавлю позже, когда появится log_box
        self.gui_handler: logging.Handler | None = None

        # глобальный обработчик необработанных исключений
        self._setup_exception_handling()

    def _setup_exception_handling(self):
        """
        Любые необработанные исключения – в лог и в GUI.
        """

        def handle_exception(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return

            msg = f"Необработанное исключение {exc_type.__name__}: {exc_value}"
            self.logger.error(msg, exc_info=(exc_type, exc_value, exc_traceback))
            try:
                self.log_message(msg, level="ERROR")
            except Exception:
                pass

        sys.excepthook = handle_exception

    # ---------------------------------- GUI логирование ---------------------------------- #

    def _setup_gui_logging(self):
        """
        Создаёт logging.Handler, который пишет только в GUI-лог.
        Вызывается после создания self.log_box.
        """
        if self.gui_handler is not None:
            return

        class GUILogHandler(logging.Handler):
            def __init__(self, gui_log_method):
                super().__init__()
                self.gui_log_method = gui_log_method

            def emit(self, record):
                try:
                    # избегаем рекурсии (сообщения, пришедшие из GUI)
                    if hasattr(record, "from_gui") and record.from_gui:
                        return
                    msg = self.format(record)
                    # чуть чистим формат — убираем timestamp/логгер
                    if " - " in msg:
                        parts = msg.split(" - ", 2)
                        if len(parts) >= 3:
                            msg = parts[2]
                    self.gui_log_method(msg)
                except Exception:
                    pass

        self.gui_handler = GUILogHandler(self._gui_only_log)
        self.gui_handler.setLevel(logging.INFO)
        self.gui_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        self.logger.addHandler(self.gui_handler)

    def log_message(self, message: str, level: str = "INFO"):
        """
        Лог в GUI + в основной логгер.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        gui_line = f"[{ts}] {message}"
        self._gui_only_log(gui_line)

        try:
            lvl = level.upper()
            extra = {"from_gui": True}
            if lvl == "ERROR":
                self.logger.error(message, extra=extra)
            elif lvl == "WARNING":
                self.logger.warning(message, extra=extra)
            elif lvl == "DEBUG":
                self.logger.debug(message, extra=extra)
            else:
                self.logger.info(message, extra=extra)
        except Exception:
            pass

    def _gui_only_log(self, message: str):
        """Пишем только в текстбокс лога."""
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.configure(state="disabled")
            self.log_box.see("end")
        except Exception:
            pass

    # ---------------------------------- ПРОГРЕСС ---------------------------------- #

    def update_progress(self, value: int, text: str = ""):
        try:
            self.progress_bar.set(max(0, min(100, value)) / 100)
            if text:
                self.progress_text.configure(text=text)
        except Exception:
            pass

    def post_ui(self, func, *args, **kwargs):
        """Безопасный вызов в UI-потоке из других потоков."""
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except Exception:
            pass

    def start_busy(self, text: str = ""):
        try:
            self.progress_bar.configure(mode="indeterminate")
            if text:
                self.progress_text.configure(text=text)
            self.progress_bar.start()
        except Exception:
            pass

    def stop_busy(self):
        try:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #   UI
    # ------------------------------------------------------------------ #

    def enrich_with_market_data(tender_data, city):
        service = SearchService()

        works = tender_data.get("technical", {}).get("works", [])
        performers_by_task = {}
        works_breakdown = []

        for w in works:
            task = w["name"]

            # Ищем исполнителей
            perf = service.search_performers_for_task(task, city)
            performers_by_task[task] = perf

            # Ищем цены
            price_info = service.search_prices(task, city)

            if price_info and "price_min" in price_info:
                pmin = price_info["price_min"]
                pmax = price_info.get("price_max", pmin)
            else:
                pmin = pmax = None

            works_breakdown.append({
                "work_name": w["name"],
                "volume": w["volume"],
                "unit": w["unit"],
                "price_min": pmin,
                "price_max": pmax,
                "subtotal_min": float(w["volume"]) * pmin if pmin else None,
                "subtotal_max": float(w["volume"]) * pmax if pmax else None,
                "status": "ok" if pmin else "no_data",
            })

        total_min = sum(x["subtotal_min"] for x in works_breakdown if x["subtotal_min"])
        total_max = sum(x["subtotal_max"] for x in works_breakdown if x["subtotal_max"])

        tender_data["performers_by_task"] = performers_by_task
        tender_data["market_analysis"] = {
            "minimum_sum_calculation": {
                "works_breakdown": works_breakdown,
                "total_min": total_min,
                "total_max": total_max,
                "currency": "RUB"
            }
        }

        return tender_data


    def _setup_ui(self):
        self.title("Tender Analyzer")
        self.geometry("1100x700")

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --------- левая колонка ---------
        left_frame = ctk.CTkFrame(self)
        left_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        left_frame.grid_rowconfigure(0, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        main_content = ctk.CTkScrollableFrame(left_frame)
        main_content.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        title_label = ctk.CTkLabel(
            main_content,
            text="Tender Analyzer",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_label.pack(anchor="w", pady=(0, 10))

        # --- выбор файлов ---
        file_frame = ctk.CTkFrame(main_content)
        file_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(file_frame, text="Файлы тендерной документации:").pack(
            anchor="w"
        )

        buttons_frame = ctk.CTkFrame(file_frame)
        buttons_frame.pack(fill="x", pady=5)

        self.select_files_button = ctk.CTkButton(
            buttons_frame,
            text="📂 Добавить файлы",
            command=self.select_files,
        )
        self.select_files_button.pack(side="left", padx=(0, 5))

        self.clear_files_button = ctk.CTkButton(
            buttons_frame,
            text="🗑 Очистить",
            command=self.clear_files,
            state="disabled",
        )
        self.clear_files_button.pack(side="left")

        self.file_list_frame = ctk.CTkFrame(file_frame)
        self.file_list_frame.pack(fill="both", expand=True, pady=(5, 0))

        self.no_files_label = ctk.CTkLabel(
            self.file_list_frame,
            text="Файлы не выбраны",
            text_color=("gray50", "gray70"),
        )
        self.no_files_label.pack(pady=10)

        # --- дополнительная информация ---
        extra_frame = ctk.CTkFrame(main_content)
        extra_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            extra_frame, text="Дополнительная информация / комментарии:"
        ).pack(anchor="w")

        self.user_text = ctk.CTkTextbox(extra_frame, height=80)
        self.user_text.pack(fill="x", pady=(5, 0))

        # --- регион / город ---
        region_frame = ctk.CTkFrame(main_content)
        region_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            region_frame, text="Город / регион (для поиска цен и исполнителей):"
        ).pack(anchor="w")

        self.region_combo = ctk.CTkComboBox(
            region_frame,
            values=self.cities_list,
            width=300,
        )
        if self.cities_list:
            self.region_combo.set(self.cities_list[0])
        self.region_combo.pack(anchor="w", pady=(5, 0))

        # ============================
        # РЕЖИМ АНАЛИЗА: С ИИ / БЕЗ ИИ
        # ============================
        mode_frame = ctk.CTkFrame(main_content)
        mode_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            mode_frame,
            text="Режим анализа:"
        ).pack(anchor="w")

        self.use_ai_var = ctk.BooleanVar(value=False)

        self.use_ai_switch = ctk.CTkSwitch(
            mode_frame,
            text="Использовать AI (OpenRouter)",
            variable=self.use_ai_var,
        )
        self.use_ai_switch.pack(anchor="w", pady=5)

        # --- кнопки управления ---
        control_frame = ctk.CTkFrame(main_content)
        control_frame.pack(fill="x", pady=15)

        self.analyze_button = ctk.CTkButton(
            control_frame,
            text="🚀 Анализировать тендер",
            command=self.analyze_tender,
        )
        self.analyze_button.pack(fill="x")

        self.save_report_button = ctk.CTkButton(
            control_frame,
            text="💾 Сохранить PDF-отчёт",
            command=self.save_report,
            state="disabled",
        )
        self.save_report_button.pack(fill="x", pady=(8, 0))

        self.open_chat_button = ctk.CTkButton(
            control_frame,
            text="💬 Открыть чат с агентом",
            command=self.open_chat,
            state="disabled",
        )
        self.open_chat_button.pack(fill="x", pady=(8, 0))

        # --------- правая колонка (прогресс + лог) ---------
        right_frame = ctk.CTkFrame(self)
        right_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # прогресс
        progress_frame = ctk.CTkFrame(right_frame)
        progress_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(
            progress_frame,
            text="Прогресс анализа:",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w")

        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.pack(fill="x", pady=(5, 2))
        self.progress_bar.set(0)

        self.progress_text = ctk.CTkLabel(
            progress_frame,
            text="Ожидание запуска анализа...",
            text_color=("gray50", "gray70"),
        )
        self.progress_text.pack(anchor="w")

        # лог
        log_frame = ctk.CTkFrame(right_frame)
        log_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_frame,
            text="Лог работы:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=5, pady=(5, 0))

        self.log_box = ctk.CTkTextbox(log_frame, wrap="word")
        self.log_box.grid(
            row=1, column=0, sticky="nsew", padx=5, pady=(5, 5)
        )
        self.log_box.configure(state="disabled")

        log_buttons = ctk.CTkFrame(log_frame)
        log_buttons.grid(row=2, column=0, sticky="ew", padx=5, pady=(0, 5))

        clear_log_btn = ctk.CTkButton(
            log_buttons, text="Очистить лог", width=120, command=self.clear_log
        )
        clear_log_btn.pack(side="left")

        export_log_btn = ctk.CTkButton(
            log_buttons, text="Экспорт лога", width=120, command=self.export_log
        )
        export_log_btn.pack(side="left", padx=(5, 0))

        # теперь можно настроить GUI-логгер
        self._setup_gui_logging()
        self.log_message("Интерфейс приложения инициализирован")

    # ------------------------------------------------------------------ #
    #   РАБОТА С ФАЙЛАМИ
    # ------------------------------------------------------------------ #

    def _refresh_file_list(self):
        """Перерисовать список файлов слева."""
        for child in self.file_list_frame.winfo_children():
            child.destroy()

        if not self.current_files:
            self.no_files_label = ctk.CTkLabel(
                self.file_list_frame,
                text="Файлы не выбраны",
                text_color=("gray50", "gray70"),
            )
            self.no_files_label.pack(pady=10)
            self.clear_files_button.configure(state="disabled")
            return

        for path in self.current_files:
            size_text = ""
            try:
                size = os.path.getsize(path)
                if size < 1024:
                    size_text = f"{size} B"
                elif size < 1024 * 1024:
                    size_text = f"{size / 1024:.1f} KB"
                else:
                    size_text = f"{size / (1024*1024):.1f} MB"
            except Exception:
                size_text = "?"

            self._create_file_widget(path, size_text)

        self.clear_files_button.configure(state="normal")

    def _create_file_widget(self, file_path: str, file_size_text: str):
        """Один файл в списке с кнопкой удаления."""
        frame = ctk.CTkFrame(self.file_list_frame)
        frame.pack(fill="x", pady=2)

        name = os.path.basename(file_path)
        label = ctk.CTkLabel(
            frame,
            text=f"📄 {name} ({file_size_text})",
            anchor="w",
        )
        label.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=4)

        def remove():
            if file_path in self.current_files:
                if messagebox.askyesno(
                    "Подтверждение",
                    f"Удалить файл '{name}' из списка?",
                    icon="question",
                ):
                    self.current_files.remove(file_path)
                    self._refresh_file_list()
                    self.log_message(f"Файл удалён: {name}")

        btn = ctk.CTkButton(
            frame,
            width=70,
            text="Удалить",
            command=remove,
        )
        btn.pack(side="right", padx=5, pady=4)

    def select_files(self):
        """Добавление файлов в список."""
        filetypes = [
            (
                "Документы",
                "*.pdf;*.doc;*.docx;*.xls;*.xlsx;*.pptx;*.html;*.htm;*.xml;*.csv",
            ),
            ("PDF", "*.pdf"),
            ("Word", "*.doc;*.docx"),
            ("Excel", "*.xls;*.xlsx"),
            ("Презентации", "*.pptx"),
            ("Web-страницы", "*.html;*.htm"),
            ("XML", "*.xml"),
            ("CSV", "*.csv"),
            ("Все файлы", "*.*"),
        ]

        files = filedialog.askopenfilenames(
            title="Выберите файлы тендерной документации",
            filetypes=filetypes,
        )

        if not files:
            self.log_message("Файлы не были выбраны")
            return

        added = 0
        for path in files:
            if path not in self.current_files:
                self.current_files.append(path)
                added += 1

        self._refresh_file_list()
        self.log_message(
            f"Добавлено {added} файлов. Всего в списке: {len(self.current_files)}"
        )

    def clear_files(self):
        if not self.current_files:
            return
        if messagebox.askyesno(
            "Подтверждение",
            f"Удалить {len(self.current_files)} файл(ов) из списка?",
            icon="question",
        ):
            self.current_files = []
            self._refresh_file_list()
            self.log_message("Все файлы удалены из списка")

    def read_file_content(self, file_path: str) -> str | None:
        try:
            ext = os.path.splitext(file_path)[1].lower()

            if ext == ".pdf":
                return read_services.read_pdf(file_path)
            if ext == ".docx":
                return read_services.read_docx(file_path)
            if ext == ".doc":
                return read_services.read_doc(file_path)

            if ext == ".xlsx":
                return read_services.read_xlsx(file_path)
            if ext == ".xls":
                return read_services.read_xls(file_path)

            if ext == ".pptx":
                return read_services.read_pptx(file_path)
            if ext in (".html", ".htm"):
                return read_services.read_html(file_path)
            if ext == ".xml":
                return read_services.read_xml(file_path)
            if ext == ".csv":
                return read_services.read_csv(file_path)

            self.log_message(f"Неизвестный формат файла: {ext}", "WARNING")
            return None
        except Exception as e:
            self.log_message(f"Ошибка при чтении файла {file_path}: {e}", "ERROR")
            return None

    # ------------------------------------------------------------------ #
    #   АНАЛИЗ
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    #   АНАЛИЗ
    # ------------------------------------------------------------------ #

    def analyze_tender(self):
        """
        Основной метод анализа тендерной документации.
        Запускается в отдельном потоке, а UI обновляется через post_ui.
        """
        self.logger.info("analyze_tender called")

        # 1. Проверки
        if self.analysis_in_progress:
            messagebox.showwarning(
                "Анализ в процессе",
                "Анализ уже выполняется. Пожалуйста, дождитесь завершения.",
            )
            return

        if not self.current_files:
            self.log_message("❌ ОШИБКА: Не выбраны файлы для анализа", level="ERROR")
            messagebox.showwarning(
                "Нет файлов", "Пожалуйста, выберите файлы для анализа."
            )
            return

        # 2. Старт анимации и общие параметры
        self.analysis_in_progress = True
        self.analyze_button.configure(state="disabled", text="Анализ...")
        self.update_progress(0, "Проверка готовности…")
        self.log_message("🚀 Запуск анализа тендерной документации…")
        self.start_busy("Инициализация анализа…")

        user_city = self.region_combo.get() if hasattr(self, "region_combo") else None
        if user_city:
            self.log_message(f"🌆 Город для анализа рынка: {user_city}")

        # Флаг «с ИИ / без ИИ»
        use_ai = bool(getattr(self, "use_ai_var", None) and self.use_ai_var.get())
        mode_text = "с AI (OpenRouter)" if use_ai else "без AI (локальный режим)"
        self.log_message(f"⚙ Режим анализа: {mode_text}")

        def worker():
            import time

            start_ts = time.time()
            analyzed_jsons: list[str] = []
            total_files = len(self.current_files)

            try:
                # --- проход по всем выбранным файлам ---
                self.post_ui(
                    self.log_message,
                    f"📁 Начинаем обработку {total_files} файлов…",
                )

                for idx, path in enumerate(self.current_files, start=1):
                    filename = os.path.basename(path)

                    # прогресс
                    self.post_ui(
                        self.update_progress,
                        int(10 + 60 * (idx / max(total_files, 1))),
                        f"Анализ файла {idx} из {total_files}: {filename}",
                    )
                    self.post_ui(
                        self.log_message,
                        f"📄 [{idx}/{total_files}] Чтение файла {filename}",
                    )

                    # чтение файла
                    try:
                        content = self.read_file_content(path)
                    except Exception as e:
                        self.post_ui(
                            self.log_message,
                            f"❌ Ошибка чтения файла {filename}: {e}",
                            "ERROR",
                        )
                        continue

                    if not content:
                        self.post_ui(
                            self.log_message,
                            f"⚠️ Файл {filename} не содержит текста после обработки.",
                            "WARNING",
                        )
                        continue

                    self.post_ui(
                        self.log_message,
                        f"✅ Файл прочитан, длина: {len(content):,} символов",
                    )

                    # логика режима
                    if use_ai:
                        self.post_ui(
                            self.log_message,
                            "🤖 Отправляем текст в LLM…",
                        )
                    else:
                        self.post_ui(
                            self.log_message,
                            "🧮 Запуск локального анализа без AI…",
                        )

                    # --- анализ текста ---
                    try:
                        analyzed_json = ai_services.analyze_text(
                            content,
                            user_city=user_city,
                            use_llm=use_ai,
                        )
                    except Exception as e:
                        self.post_ui(
                            self.log_message,
                            f"❌ Ошибка анализа файла {filename}: {e}",
                            "ERROR",
                        )
                        analyzed_json = ""

                    # если ИИ/локальный анализ ничего не дал
                    if not analyzed_json or analyzed_json.strip() in ("{}", "[]"):
                        if use_ai:
                            self.post_ui(
                                self.log_message,
                                f"⚠️ AI не вернул структурированных данных для файла {filename}.",
                                "WARNING",
                            )
                        else:
                            self.post_ui(
                                self.log_message,
                                f"⚠️ Локальный анализ не нашёл структурированных данных в файле {filename}.",
                                "WARNING",
                            )
                        continue

                    analyzed_jsons.append(analyzed_json)
                    self.post_ui(
                        self.log_message,
                        f"✅ Анализ файла {filename} завершен",
                    )

                # --- агрегация результатов по всем файлам ---
                if not analyzed_jsons:
                    self.post_ui(
                        self.log_message,
                        "⚠️ Не удалось получить структурированные данные ни по одному файлу. "
                        "Будет сформирован пустой отчёт.",
                        "WARNING",
                    )
                    aggregated = json.dumps({}, ensure_ascii=False, indent=2)
                else:
                    self.post_ui(
                        self.update_progress,
                        80,
                        "Объединяем результаты анализа…",
                    )
                    self.post_ui(
                        self.log_message,
                        f"🔄 Объединяем результаты {len(analyzed_jsons)} файлов…",
                    )
                    try:
                        aggregated = ai_services.summarize_jsons(analyzed_jsons)
                    except Exception as e:
                        self.post_ui(
                            self.log_message,
                            f"❌ Ошибка агрегации JSON: {e}",
                            "ERROR",
                        )
                        aggregated = json.dumps({}, ensure_ascii=False, indent=2)

                # --- сохраняем агрегированный JSON, чтобы потом делать PDF/чат ---
                temp_dir = os.path.join(os.getcwd(), "temp")
                os.makedirs(temp_dir, exist_ok=True)
                aggregated_path = os.path.join(temp_dir, "aggregated_tender.json")

                try:
                    with open(aggregated_path, "w", encoding="utf-8") as f:
                        f.write(aggregated)
                except Exception as e:
                    self.post_ui(
                        self.log_message,
                        f"❌ Не удалось сохранить aggregated_tender.json: {e}",
                        "ERROR",
                    )
                    # но всё равно попробуем продолжить, чтобы UI не завис

                # держим в объекте приложения
                self.aggregated_json_path = aggregated_path
                self.analyzed_data = aggregated

                self.post_ui(
                    self.update_progress,
                    100,
                    "Анализ завершён успешно!",
                )
                self.post_ui(
                    self.log_message,
                    "🎉 Анализ тендерной документации завершён",
                )

                # кнопки отчёта / чата
                self.post_ui(self.save_report_button.configure, state="normal")
                if use_ai:
                    self.post_ui(self.open_chat_button.configure, state="normal")
                else:
                    self.post_ui(self.open_chat_button.configure, state="disabled")

            except Exception as e:
                # на всякий случай ловим любые падения worker'а
                self.post_ui(
                    self.log_message,
                    f"КРИТИЧЕСКАЯ ОШИБКА В WORKER: {e}",
                    "ERROR",
                )
                self.post_ui(
                    messagebox.showerror,
                    "Ошибка анализа",
                    f"Во время анализа произошла ошибка:\n{e}",
                )
            finally:
                duration = time.time() - start_ts
                self.post_ui(
                    self.log_message,
                    f"⏱ Время анализа: {duration:.1f} сек.",
                )

                def _finalize_ui():
                    self.analysis_in_progress = False
                    self.analyze_button.configure(
                        state="normal", text="🚀 Анализировать тендер"
                    )
                    self.stop_busy()

                self.post_ui(_finalize_ui)

        # 3. Запуск фонового потока
        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ #

    def save_report(self):
        if not self.aggregated_json_path or not os.path.exists(
            self.aggregated_json_path
        ):
            messagebox.showwarning(
                "Нет данных",
                "Сначала выполните анализ тендерной документации.",
            )
            return

        out_path = filedialog.asksaveasfilename(
            title="Сохранить отчёт как…",
            defaultextension=".pdf",
            filetypes=[("PDF файлы", "*.pdf")],
        )
        if not out_path:
            return

        try:
            self.log_message("Генерируем PDF-отчёт…")
            generate_report.generate_pdf_report(
                self.aggregated_json_path, out_path
            )
            self.log_message(f"PDF-отчёт сохранён: {out_path}")
            messagebox.showinfo(
                "Отчёт сохранён",
                f"PDF-отчёт успешно сохранён:\n{out_path}",
            )
            if messagebox.askyesno(
                "Открыть отчёт", "Открыть созданный PDF-файл?"
            ):
                os.startfile(out_path)
        except Exception as e:
            msg = f"Ошибка при создании отчёта: {e}"
            self.log_message(msg, "ERROR")
            messagebox.showerror("Ошибка", msg)

    def open_chat(self):
        if not self.aggregated_json_path or not os.path.exists(
            self.aggregated_json_path
        ):
            messagebox.showwarning(
                "Нет данных",
                "Сначала выполните анализ тендерной документации.",
            )
            return
        ChatWindow(self)

    # ------------------------------------------------------------------ #
    #   ЛОГ
    # ------------------------------------------------------------------ #

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def export_log(self):
        content = self.log_box.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showinfo("Пустой лог", "Лог пуст, экспортировать нечего.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить лог как…",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo(
                "Экспорт лога", f"Лог успешно сохранён:\n{path}"
            )
        except Exception as e:
            messagebox.showerror(
                "Ошибка экспорта", f"Не удалось сохранить лог:\n{e}"
            )


# ======================================================================
#   ОКНО ЧАТА
# ======================================================================


class ChatWindow(ctk.CTkToplevel):
    """
    Простое чат-окно, которое ходит в ai_services.chat_with_model().
    """

    def __init__(self, parent: TenderAnalyzerApp):
        super().__init__(parent)
        self.parent = parent

        self.title("Чат с AI-агентом")
        self.geometry("800x600")
        self.transient(parent)
        self.grab_set()

        self._setup_ui()

    def _setup_ui(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        title = ctk.CTkLabel(
            frame,
            text="💬 Чат с AI-агентом по результатам анализа тендера",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(pady=(0, 10))

        self.chat_box = ctk.CTkTextbox(frame, height=400, wrap="word")
        self.chat_box.pack(fill="both", expand=True, padx=5, pady=(0, 10))
        self.chat_box.configure(state="disabled")

        input_frame = ctk.CTkFrame(frame)
        input_frame.pack(fill="x", pady=(0, 5))

        self.message_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="Введите вопрос по тендеру…",
        )
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.message_entry.bind("<Return>", self._on_send_enter)

        send_btn = ctk.CTkButton(
            input_frame, text="Отправить", width=120, command=self.send_message
        )
        send_btn.pack(side="right")

    def _append_chat(self, prefix: str, text: str):
        self.chat_box.configure(state="normal")
        self.chat_box.insert("end", f"{prefix}: {text}\n\n")
        self.chat_box.configure(state="disabled")
        self.chat_box.see("end")

    def _on_send_enter(self, event):
        self.send_message()

    def send_message(self):
        msg = self.message_entry.get().strip()
        if not msg:
            return
        self.message_entry.delete(0, "end")
        self._append_chat("Вы", msg)

        def worker(user_msg: str):
            try:
                answer = ai_services.chat_with_model(user_msg)
            except Exception as e:
                answer = f"Ошибка при обращении к модели: {e}"
            self.after(0, lambda: self._append_chat("AI", answer))

        threading.Thread(target=worker, args=(msg,), daemon=True).start()


# ======================================================================
#   Точка входа
# ======================================================================


def main():
    app = TenderAnalyzerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
