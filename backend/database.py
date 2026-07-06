import sqlite3
import os
import csv
from datetime import datetime
from cryptography.fernet import Fernet

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eye_monitor.db")
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".key")

class Database:
    def __init__(self):
        self._init_key()
        self._init_db()

    def _init_key(self):
        """Initialize or load the encryption key."""
        if not os.path.exists(KEY_PATH):
            key = Fernet.generate_key()
            with open(KEY_PATH, "wb") as key_file:
                key_file.write(key)
        else:
            with open(KEY_PATH, "rb") as key_file:
                key = key_file.read()
        self.cipher = Fernet(key)

    def encrypt(self, data: str) -> str:
        """Encrypt string data."""
        if not data:
            return ""
        return self.cipher.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypt string token."""
        if not token:
            return ""
        try:
            return self.cipher.decrypt(token.encode()).decode()
        except Exception:
            return "[Decryption Error]"

    def _init_db(self):
        """Create database tables if they do not exist."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Table for hourly/session metric rollups
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                avg_bpm REAL,
                avg_ear REAL,
                avg_perclos REAL,
                avg_posture_score REAL
            )
        """)
        
        # Table for alert history. Sensitive fields (like window_title) are encrypted.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                bpm REAL NOT NULL,
                window_title TEXT,
                resolved INTEGER DEFAULT 0
            )
        """)

        # Table for user baseline tuning
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_baseline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                resting_bpm REAL NOT NULL,
                calibrated_ear_threshold REAL NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()

    def log_metrics(self, bpm: float, ear: float, perclos: float, posture_score: float):
        """Log average session metrics."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO metrics_log (timestamp, avg_bpm, avg_ear, avg_perclos, avg_posture_score) VALUES (?, ?, ?, ?, ?)",
            (timestamp, bpm, ear, perclos, posture_score)
        )
        conn.commit()
        conn.close()

    def log_alert(self, alert_type: str, bpm: float, window_title: str, resolved: bool = False):
        """Log an alert event. Encrypts sensitive window title."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        encrypted_title = self.encrypt(window_title) if window_title else ""
        cursor.execute(
            "INSERT INTO alert_log (timestamp, alert_type, bpm, window_title, resolved) VALUES (?, ?, ?, ?, ?)",
            (timestamp, alert_type, bpm, encrypted_title, 1 if resolved else 0)
        )
        conn.commit()
        conn.close()

    def update_last_alert_resolved(self):
        """Mark the most recent alert as resolved (i.e. break taken)."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE alert_log SET resolved = 1 WHERE id = (SELECT MAX(id) FROM alert_log)"
        )
        conn.commit()
        conn.close()

    def log_baseline(self, resting_bpm: float, ear_threshold: float):
        """Log user calibrated baseline parameters."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO user_baseline (timestamp, resting_bpm, calibrated_ear_threshold) VALUES (?, ?, ?)",
            (timestamp, resting_bpm, ear_threshold)
        )
        conn.commit()
        conn.close()

    def get_latest_baseline(self):
        """Get the most recent calibration settings."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT resting_bpm, calibrated_ear_threshold FROM user_baseline ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"resting_bpm": row[0], "ear_threshold": row[1]}
        return None

    def get_alerts(self, limit: int = 50):
        """Fetch alert logs, decrypting window titles."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, alert_type, bpm, window_title, resolved FROM alert_log ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        alerts = []
        for r in rows:
            alerts.append({
                "id": r[0],
                "timestamp": r[1],
                "alert_type": r[2],
                "bpm": r[3],
                "window_title": self.decrypt(r[4]) if r[4] else "",
                "resolved": bool(r[5])
            })
        return alerts

    def get_metrics_history(self, limit: int = 100):
        """Fetch metrics logs."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, avg_bpm, avg_ear, avg_perclos, avg_posture_score FROM metrics_log ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for r in rows:
            history.append({
                "timestamp": r[0],
                "avg_bpm": r[1],
                "avg_ear": r[2],
                "avg_perclos": r[3],
                "avg_posture_score": r[4]
            })
        # Return chronological order
        history.reverse()
        return history

    def clear_logs(self):
        """Clear metrics and alert logs."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM metrics_log")
        cursor.execute("DELETE FROM alert_log")
        conn.commit()
        conn.close()

    def export_csv(self, export_path: str) -> str:
        """Export alert and metric history as a combined CSV zip/report format."""
        try:
            alerts = self.get_alerts(1000)
            with open(export_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Type", "Timestamp", "Detail 1", "Detail 2", "Detail 3"])
                writer.writerow(["--- ALERTS ---"])
                writer.writerow(["ID", "Timestamp", "Alert Type", "BPM", "Window", "Resolved"])
                for a in alerts:
                    writer.writerow([a["id"], a["timestamp"], a["alert_type"], a["bpm"], a["window_title"], a["resolved"]])
                
                writer.writerow([])
                writer.writerow(["--- METRICS HISTORY ---"])
                writer.writerow(["Timestamp", "Avg BPM", "Avg EAR", "Avg PERCLOS", "Avg Posture Score"])
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT timestamp, avg_bpm, avg_ear, avg_perclos, avg_posture_score FROM metrics_log ORDER BY id DESC LIMIT 5000")
                for r in cursor.fetchall():
                    writer.writerow(r)
                conn.close()
            return export_path
        except Exception as e:
            return str(e)
