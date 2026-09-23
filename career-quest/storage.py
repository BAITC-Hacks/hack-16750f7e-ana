"""One transactional SQLite snapshot; sessions and credentials are never stored."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing

FIELDS = ("employees", "skills", "events", "history", "paused_employees", "data_source", "revision")


class SnapshotStore:
    def __init__(self, path):
        self.path = path
        try:
            exists = Path(path).exists()
            if exists and Path(path).stat().st_size == 0:
                raise sqlite3.DatabaseError("empty existing database")
            with closing(sqlite3.connect(path)) as db, db:
                if exists and not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='snapshot'").fetchone():
                    raise sqlite3.DatabaseError("snapshot table missing")
                db.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        except sqlite3.Error as exc:
            raise RuntimeError("Хранилище недоступно или повреждено. Сохраните копию файла и восстановите резервную копию; автоматический сброс запрещён.") from exc

    def load(self):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                row = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            if set(data) != set(FIELDS) or not isinstance(data["paused_employees"], list):
                raise ValueError("snapshot schema")
            return data
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise RuntimeError("Повреждённое сохранение: восстановите резервную копию. Исходный файл оставлен без изменений.") from exc

    def save(self, engine):
        data = {key: sorted(engine.paused_employees) if key == "paused_employees" else getattr(engine, key) for key in FIELDS}
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?)", (json.dumps(data, ensure_ascii=False, allow_nan=False),))
        except (sqlite3.Error, ValueError) as exc:
            raise RuntimeError("Не удалось сохранить изменения. Действие отменено; проверьте доступ к хранилищу.") from exc
