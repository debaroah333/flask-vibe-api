import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import text
from werkzeug.exceptions import HTTPException

from extensions import db


def json_response(code: int, message: str, data=None):
    return jsonify(
        {
            "code": code,
            "message": message,
            "data": data,
        }
    ), code


def create_app():
    app = Flask(__name__)
    app.json.ensure_ascii = False

    # 若程序環境變數中尚未設定 DATABASE_URL，嘗試載入專案目錄的 .env
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少環境變數 DATABASE_URL，無法啟動應用程式。請在環境變數或 .env 檔案中指定有效的 PostgreSQL 連線字串。")

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    import re
    from urllib.parse import urlsplit
    from flask import make_response, request

    def normalize_origin(val: str) -> str:
        if not val:
            return ""
        val = val.strip()
        parsed = urlsplit(val)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".lower()
        return val.rstrip("/").lower()

    def is_allowed_origin(origin: str) -> bool:
        if not origin:
            return False

        norm_origin = normalize_origin(origin)

        # 預設允許本機開發常用網段 (localhost, 127.x.x.x, 0.0.0.0 等)
        if re.match(r"^https?://(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0)(:\d+)?$", norm_origin):
            return True

        web_origin = os.getenv("WEB_ORIGIN")
        if not web_origin:
            return False

        # 支援以逗號、分號、換行或空白分隔的多個網域設定
        allowed_items = [item.strip() for item in re.split(r"[,;\s]+", web_origin) if item.strip()]
        parsed_origin = urlsplit(norm_origin)
        origin_host = (parsed_origin.netloc or norm_origin).split(":")[0].lower()

        for item in allowed_items:
            if item == "*":
                return True

            norm_item = normalize_origin(item)
            if norm_origin == norm_item:
                return True

            # 針對可能只輸入主機名或萬用字元（例如 debaroah333.github.io, *.github.io, github.io）的比對
            parsed_item = urlsplit(norm_item)
            item_host = (parsed_item.netloc if "://" in norm_item else norm_item).split(":")[0].lower()

            if item_host == origin_host:
                return True
            if item_host in ("github.io", "*.github.io") and origin_host.endswith(".github.io"):
                return True
            if item_host.startswith("*.") and origin_host.endswith(item_host[1:]):
                return True

        return False

    @app.before_request
    def handle_cors_preflight():
        if request.method == "OPTIONS":
            origin = request.headers.get("Origin")
            response = make_response("", 204)
            if origin and is_allowed_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
                req_headers = request.headers.get("Access-Control-Request-Headers")
                if req_headers:
                    response.headers["Access-Control-Allow-Headers"] = req_headers
                else:
                    response.headers["Access-Control-Allow-Headers"] = "*"
                response.headers["Access-Control-Max-Age"] = "86400"
                response.headers["Access-Control-Allow-Private-Network"] = "true"
            response.headers["Vary"] = "Origin"
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return response

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        if origin and is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    from routes import api_bp
    app.register_blueprint(api_bp)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        if error.code == 404:
            return json_response(404, "找不到這一筆", None)
        return json_response(error.code, error.description, None)

    @app.get("/api/health")
    def health_check():
        return json_response(
            200,
            "伺服器運作正常，測試正常可以進行其他任務。",
            {
                "status": "ok",
                "time": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.get("/api/health/db")
    def health_db_check():
        try:
            db.session.execute(text("SELECT 1"))
            return json_response(
                200,
                "資料庫連線正常",
                {"status": "ok"},
            )
        except Exception as e:
            app.logger.error("資料庫連線檢查失敗: %s", e, exc_info=True)
            return json_response(
                500,
                "資料庫暫時連不上",
                None,
            )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)