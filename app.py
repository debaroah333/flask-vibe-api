
from datetime import datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)

# 讓 JSON 回應中的中文直接顯示，不轉換成 Unicode escape
app.json.ensure_ascii = False

# In-memory 模擬資料
tasks = [
    {
        "id": 1,
        "title": "學習 Flask",
        "description": "建立第一個 RESTful API",
    },
    {
        "id": 2,
        "title": "練習 Python",
        "description": "熟悉字典、清單與函式",
    },
]

# 下一筆任務使用的 ID
next_task_id = 3


def make_response(status, message, data, http_status):
    """統一 API JSON 回應格式。"""
    return jsonify({
        "status": status,
        "message": message,
        "data": data,
    }), http_status


@app.get("/api/health")
def health_check():
    """健康檢查：回傳服務狀態與 UTC 時間。"""
    return make_response(
        "success",
        "API 運作正常",
        {
            "server": "running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        200,
    )


@app.get("/api/tasks")
def get_tasks():
    """取得所有任務。"""
    return make_response(
        "success",
        "成功取得任務清單",
        tasks,
        200,
    )


@app.post("/api/tasks")
def create_task():
    """新增任務。"""
    global next_task_id

    # 要求請求內容為合法 JSON
    if not request.is_json:
        return make_response(
            "error",
            "請使用 JSON 格式傳送請求",
            None,
            400,
        )

    # 靜默解析關閉，讓不合法的 JSON 由 Flask 回傳 400
    payload = request.get_json(silent=False)

    if not isinstance(payload, dict):
        return make_response(
            "error",
            "JSON Body 必須是物件",
            None,
            400,
        )

    # 驗證必填欄位 title
    title = payload.get("title")

    if not isinstance(title, str) or not title.strip():
        return make_response(
            "error",
            "缺少必填欄位 title，或 title 不可為空",
            None,
            400,
        )

    description = payload.get("description", "")

    if not isinstance(description, str):
        return make_response(
            "error",
            "description 必須是字串",
            None,
            400,
        )

    # 建立任務
    new_task = {
        "id": next_task_id,
        "title": title.strip(),
        "description": description.strip(),
    }

    tasks.append(new_task)
    next_task_id += 1

    return make_response(
        "success",
        "任務新增成功",
        new_task,
        201,
    )


@app.get("/api/tasks/<int:task_id>")
def get_task(task_id):
    """根據 ID 取得單一任務。"""
    task = next(
        (item for item in tasks if item["id"] == task_id),
        None,
    )

    if task is None:
        return make_response(
            "error",
            f"找不到 ID 為 {task_id} 的任務",
            None,
            404,
        )

    return make_response(
        "success",
        "成功取得任務",
        task,
        200,
    )


@app.errorhandler(404)
def handle_not_found(error):
    """統一處理不存在的路徑。"""
    return make_response(
        "error",
        "找不到指定的 API 路徑",
        None,
        404,
    )


@app.errorhandler(400)
def handle_bad_request(error):
    """統一處理 Flask 產生的 400 錯誤，例如 JSON 格式錯誤。"""
    return make_response(
        "error",
        "請求格式錯誤或 JSON 格式不合法",
        None,
        400,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
