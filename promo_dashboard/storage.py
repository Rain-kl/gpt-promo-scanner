"""Private SQLite persistence for settings, jobs, logs, and incremental results."""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from promo_dashboard.models import Settings

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_STATES = ("starting", "running", "pausing", "paused", "stopping")


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "dashboard.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, time TEXT, message TEXT);
                CREATE INDEX IF NOT EXISTS logs_job ON logs(job_id, id);
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, data TEXT);
                CREATE INDEX IF NOT EXISTS results_job ON results(job_id, id);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
        if row:
            return Settings.model_validate_json(row["data"])
        # Import a legacy config/environment once, keeping existing CLI users compatible.
        import config

        try:
            token = config.get_token()
        except ValueError:
            token = ""
        settings = Settings(
            token=token,
            proxy_url=config.get_proxy_url(),
            clash_socket=config.get_clash_socket(),
            rule_group=config.get_proxy_group("rule"),
            global_group=config.get_proxy_group("global"),
        )
        self.save_settings(settings)
        return settings

    def save_settings(self, settings):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (1, ?)", (settings.model_dump_json(),)
            )

    def save_job(self, job):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?)",
                (job["id"], json.dumps(job, ensure_ascii=False)),
            )

    def job(self, job_id):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def jobs(self):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def update_job(self, job_id, **changes):
        with self.lock:
            job = self.job(job_id)
            if job:
                job.update(changes)
                self.save_job(job)
            return job

    def log(self, job_id, message):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO logs(job_id, time, message) VALUES (?, ?, ?)",
                (job_id, now(), message[:8000]),
            )
            # Keep bounded logs; result rows and job summaries remain complete.
            db.execute(
                "DELETE FROM logs WHERE job_id=? AND id NOT IN "
                "(SELECT id FROM logs WHERE job_id=? ORDER BY id DESC LIMIT 5000)",
                (job_id, job_id),
            )

    def logs(self, job_id, after=0):
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT id, time, message FROM logs WHERE job_id=? AND id>? ORDER BY id LIMIT 500",
                (job_id, after),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_result(self, job_id, result):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO results(job_id, data) VALUES (?, ?)",
                (job_id, json.dumps(result, ensure_ascii=False)),
            )

    def results(self, job_id):
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT id, data FROM results WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()
        return [dict(json.loads(row["data"]), id=row["id"]) for row in rows]

    def delete_job(self, job_id):
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM logs WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM results WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
