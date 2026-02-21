#!/usr/bin/env python3
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DB_PATH = Path(__file__).with_name("wrp.db")
INDEX_PATH = Path(__file__).with_name("index.html")


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vpns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                creator TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vpn_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(vpn_id) REFERENCES vpns(id) ON DELETE CASCADE
            );
            """
        )


def seed_if_empty():
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM vpns").fetchone()["c"]
        if count:
            return
        now = datetime.now(timezone.utc)
        rows = [
            ("vpn-europe-production.ovpn", "SuperAdmin", now + timedelta(minutes=60)),
            ("vpn-project-temp.conf", "Admin", now + timedelta(minutes=25)),
        ]
        conn.executemany(
            "INSERT INTO vpns(name, creator, expires_at) VALUES(?,?,?)",
            [(name, creator, exp.isoformat()) for name, creator, exp in rows],
        )
        conn.commit()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: bytes, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _not_found(self, path: str):
        if path.startswith("/api/"):
            return self._send_json({"error": f"route inconnue: {path}"}, 404)
        html = f"""<!doctype html><html lang='fr'><meta charset='utf-8'>
        <title>404 - Not Found</title>
        <body style='font-family:Arial;padding:24px;background:#0d1428;color:#eef3ff'>
        <h1>404 - Not Found</h1><p>Route introuvable: <code>{path}</code></p>
        <p><a href='/' style='color:#8ec5ff'>Retour à l'accueil</a></p></body></html>""".encode("utf-8")
        return self._send_html(html, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            return self._send_html(INDEX_PATH.read_bytes())

        if path == "/health":
            return self._send_json({"status": "ok"})

        if path == "/api/vpns":
            with db_conn() as conn:
                rows = conn.execute(
                    "SELECT id,name,creator,expires_at,created_at FROM vpns ORDER BY id DESC"
                ).fetchall()
            return self._send_json([dict(r) for r in rows])

        if path == "/api/comments":
            vpn_id = parse_qs(parsed.query).get("vpn_id", [None])[0]
            if not vpn_id:
                return self._send_json({"error": "vpn_id requis"}, 400)
            with db_conn() as conn:
                rows = conn.execute(
                    "SELECT id,vpn_id,author,content,created_at FROM comments WHERE vpn_id=? ORDER BY id DESC",
                    (vpn_id,),
                ).fetchall()
            return self._send_json([dict(r) for r in rows])

        return self._not_found(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._read_json()

        if parsed.path == "/api/vpns":
            name = (data.get("name") or "").strip()
            creator = (data.get("creator") or "Creator").strip() or "Creator"
            try:
                ttl_minutes = int(data.get("ttl_minutes") or 30)
            except (TypeError, ValueError):
                return self._send_json({"error": "ttl_minutes invalide"}, 400)

            if not name:
                return self._send_json({"error": "name requis"}, 400)
            if ttl_minutes < 1 or ttl_minutes > 1440:
                return self._send_json({"error": "ttl_minutes doit être entre 1 et 1440"}, 400)

            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
            with db_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO vpns(name, creator, expires_at) VALUES(?,?,?)",
                    (name, creator, expires_at),
                )
                conn.commit()
            return self._send_json({"ok": True, "id": cur.lastrowid}, 201)

        if parsed.path == "/api/comments":
            vpn_id = data.get("vpn_id")
            author = (data.get("author") or "Utilisateur").strip() or "Utilisateur"
            content = (data.get("content") or "").strip()[:240]
            if not vpn_id or not content:
                return self._send_json({"error": "vpn_id et content requis"}, 400)

            with db_conn() as conn:
                exists = conn.execute("SELECT 1 FROM vpns WHERE id=?", (vpn_id,)).fetchone()
                if not exists:
                    return self._send_json({"error": "vpn introuvable"}, 404)
                conn.execute(
                    "INSERT INTO comments(vpn_id, author, content) VALUES(?,?,?)",
                    (vpn_id, author, content),
                )
                conn.commit()
            return self._send_json({"ok": True}, 201)

        return self._not_found(parsed.path)


def run():
    init_db()
    seed_if_empty()
    server = HTTPServer(("0.0.0.0", 8000), Handler)
    print("WRP server on http://0.0.0.0:8000")
    server.serve_forever()


if __name__ == "__main__":
    run()
