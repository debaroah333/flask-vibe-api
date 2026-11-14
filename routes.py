from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Account, Transfer

api_bp = Blueprint("api", __name__, url_prefix="/api")


def json_response(code: int, message: str, data=None):
    return (
        jsonify(
            {
                "code": code,
                "message": message,
                "data": data,
            }
        ),
        code,
    )


# ==========================================
# 帳戶相關路由 (Accounts)
# ==========================================


@api_bp.get("/accounts")
def get_accounts():
    try:
        accounts = Account.query.order_by(Account.id.asc()).all()
        return json_response(200, "成功取得帳戶清單", [a.to_dict() for a in accounts])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/accounts")
def create_account():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填驗證：name
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return json_response(400, "缺少必填欄位或欄位為空：name（帳戶名稱）", None)
    name = name.strip()
    if len(name) > 50:
        return json_response(400, "帳戶名稱長度不可超過 50 個字元", None)

    # 必填驗證：kind
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        return json_response(400, "缺少必填欄位或欄位為空：kind（帳戶種類）", None)
    kind = kind.strip()
    allowed_kinds = {"cash", "bank", "credit_card", "broker"}
    if kind not in allowed_kinds:
        return json_response(
            400, "帳戶種類不合法，僅允許 cash、bank、credit_card、broker", None
        )

    # 重複名稱檢查
    try:
        existing = Account.query.filter_by(name=name).first()
        if existing:
            return json_response(400, "帳戶名稱已存在，請使用其他名稱", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    # 選填：currency（預設 TWD）
    raw_currency = payload.get("currency")
    if raw_currency is None:
        currency = "TWD"
    else:
        if not isinstance(raw_currency, str) or len(raw_currency.strip()) != 3:
            return json_response(400, "幣別代碼必須為 3 碼英文字母", None)
        currency = raw_currency.strip().upper()

    # 選填：opening_balance（預設 0）
    raw_balance = payload.get("opening_balance")
    if raw_balance is None:
        opening_balance = Decimal("0")
    else:
        try:
            opening_balance = Decimal(str(raw_balance).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "期初餘額格式不正確，必須為數值", None)

    # 選填：statement_day（1 到 31）
    raw_statement_day = payload.get("statement_day")
    if raw_statement_day is None:
        statement_day = None
    else:
        try:
            statement_day = int(raw_statement_day)
            if not (1 <= statement_day <= 31):
                return json_response(400, "結帳日必須介於 1 到 31 之間", None)
        except (ValueError, TypeError):
            return json_response(400, "結帳日格式不正確，必須是整數", None)

    # 選填：payment_day（1 到 31）
    raw_payment_day = payload.get("payment_day")
    if raw_payment_day is None:
        payment_day = None
    else:
        try:
            payment_day = int(raw_payment_day)
            if not (1 <= payment_day <= 31):
                return json_response(400, "繳款日必須介於 1 到 31 之間", None)
        except (ValueError, TypeError):
            return json_response(400, "繳款日格式不正確，必須是整數", None)

    account = Account(
        name=name,
        kind=kind,
        currency=currency,
        opening_balance=opening_balance,
        statement_day=statement_day,
        payment_day=payment_day,
    )

    try:
        db.session.add(account)
        db.session.commit()
        return json_response(201, "帳戶新增成功", account.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "帳戶資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/accounts/<int:account_id>")
def get_account(account_id):
    try:
        account = db.session.get(Account, account_id)
        if not account:
            return json_response(404, "找不到指定的帳戶", None)
        return json_response(200, "成功取得帳戶資料", account.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/accounts/<int:account_id>")
def update_account(account_id):
    try:
        account = db.session.get(Account, account_id)
        if not account:
            return json_response(404, "找不到指定的帳戶", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        if "name" in payload:
            new_name = payload["name"]
            if (
                new_name is None
                or not isinstance(new_name, str)
                or not new_name.strip()
            ):
                return json_response(400, "帳戶名稱不可為空", None)
            new_name = new_name.strip()
            if len(new_name) > 50:
                return json_response(400, "帳戶名稱長度不可超過 50 個字元", None)
            dup = Account.query.filter(
                Account.name == new_name, Account.id != account_id
            ).first()
            if dup:
                return json_response(400, "帳戶名稱已存在，請使用其他名稱", None)
            account.name = new_name

        if "statement_day" in payload:
            raw_sday = payload["statement_day"]
            if raw_sday is None:
                account.statement_day = None
            else:
                try:
                    sday = int(raw_sday)
                    if not (1 <= sday <= 31):
                        return json_response(400, "結帳日必須介於 1 到 31 之間", None)
                    account.statement_day = sday
                except (ValueError, TypeError):
                    return json_response(400, "結帳日格式不正確，必須是整數", None)

        if "payment_day" in payload:
            raw_pday = payload["payment_day"]
            if raw_pday is None:
                account.payment_day = None
            else:
                try:
                    pday = int(raw_pday)
                    if not (1 <= pday <= 31):
                        return json_response(400, "繳款日必須介於 1 到 31 之間", None)
                    account.payment_day = pday
                except (ValueError, TypeError):
                    return json_response(400, "繳款日格式不正確，必須是整數", None)

        account.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "帳戶更新成功", account.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/accounts/<int:account_id>/balance")
def get_account_balance(account_id):
    try:
        account = db.session.get(Account, account_id)
        if not account:
            return json_response(404, "找不到指定的帳戶", None)

        incoming = Transfer.query.filter_by(to_account_id=account.id).all()
        outgoing = Transfer.query.filter_by(from_account_id=account.id).all()

        opening_balance = (
            account.opening_balance
            if account.opening_balance is not None
            else Decimal("0")
        )
        in_amount = sum((t.amount for t in incoming), Decimal("0"))
        out_amount = sum((t.amount for t in outgoing), Decimal("0"))
        original_balance = opening_balance + in_amount - out_amount

        in_twd = sum((t.amount * t.fx_rate_to_twd for t in incoming), Decimal("0"))
        out_twd = sum((t.amount * t.fx_rate_to_twd for t in outgoing), Decimal("0"))

        if account.currency == "TWD":
            twd_balance = opening_balance + in_twd - out_twd
        else:
            twd_balance = in_twd - out_twd

        data = {
            "account_id": account.id,
            "account_name": account.name,
            "currency": account.currency,
            "opening_balance": float(opening_balance),
            "balance": float(original_balance),
            "original_balance": float(original_balance),
            "balance_twd": float(twd_balance),
            "twd_balance": float(twd_balance),
        }
        return json_response(200, "成功取得帳戶餘額", data)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 移轉相關路由 (Transfers)
# ==========================================


@api_bp.get("/transfers")
def get_transfers():
    try:
        transfers = Transfer.query.order_by(Transfer.id.asc()).all()
        return json_response(200, "成功取得移轉清單", [t.to_dict() for t in transfers])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/transfers")
def create_transfer():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：日期
    raw_date = payload.get("transferred_on")
    if not raw_date:
        return json_response(400, "缺少必填欄位：transferred_on（移轉日期）", None)
    try:
        transferred_on = date.fromisoformat(str(raw_date).strip())
    except (ValueError, TypeError):
        return json_response(400, "移轉日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 必填：金額
    raw_amount = payload.get("amount")
    if raw_amount is None:
        return json_response(400, "缺少必填欄位：amount（移轉金額）", None)
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "移轉金額格式不正確，必須為數值", None)
    if amount <= 0:
        return json_response(400, "移轉金額必須大於 0", None)

    # 必填：幣別
    raw_currency = payload.get("currency")
    if (
        not raw_currency
        or not isinstance(raw_currency, str)
        or len(raw_currency.strip()) != 3
    ):
        return json_response(
            400,
            "缺少必填欄位或格式錯誤：currency（幣別代碼必須為 3 碼英文字母）",
            None,
        )
    currency = raw_currency.strip().upper()

    # 必填：匯率
    raw_fx = payload.get("fx_rate_to_twd")
    if raw_fx is None:
        return json_response(400, "缺少必填欄位：fx_rate_to_twd（台幣匯率）", None)
    try:
        fx_rate_to_twd = Decimal(str(raw_fx).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "台幣匯率格式不正確，必須為數值", None)
    if fx_rate_to_twd <= 0:
        return json_response(400, "台幣匯率必須大於 0", None)

    # 必填：轉出與轉入帳戶
    raw_from = payload.get("from_account_id")
    if raw_from is None:
        return json_response(400, "缺少必填欄位：from_account_id（轉出帳戶）", None)
    try:
        from_account_id = int(raw_from)
    except (ValueError, TypeError):
        return json_response(400, "轉出帳戶 ID 必須為整數", None)

    raw_to = payload.get("to_account_id")
    if raw_to is None:
        return json_response(400, "缺少必填欄位：to_account_id（轉入帳戶）", None)
    try:
        to_account_id = int(raw_to)
    except (ValueError, TypeError):
        return json_response(400, "轉入帳戶 ID 必須為整數", None)

    # 轉出與轉入不可相同
    if from_account_id == to_account_id:
        return json_response(400, "轉出帳戶與轉入帳戶不可相同", None)

    # 檢查帳戶是否存在
    try:
        from_acc = db.session.get(Account, from_account_id)
        if not from_acc:
            return json_response(400, "指定的轉出帳戶不存在", None)
        to_acc = db.session.get(Account, to_account_id)
        if not to_acc:
            return json_response(400, "指定的轉入帳戶不存在", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    # 選填：note
    note = payload.get("note")
    if note is not None:
        note = str(note).strip() or None

    transfer = Transfer(
        transferred_on=transferred_on,
        amount=amount,
        currency=currency,
        fx_rate_to_twd=fx_rate_to_twd,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        note=note,
    )

    try:
        db.session.add(transfer)
        db.session.commit()
        return json_response(201, "移轉記錄新增成功", transfer.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(
            400, "移轉資料違反約束條件（例如帳戶不存在或轉出轉入相同）", None
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/transfers/<int:transfer_id>")
def get_transfer(transfer_id):
    try:
        transfer = db.session.get(Transfer, transfer_id)
        if not transfer:
            return json_response(404, "找不到指定的移轉記錄", None)
        return json_response(200, "成功取得移轉資料", transfer.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)
