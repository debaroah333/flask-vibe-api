from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Account,
    Expense,
    ExpenseCategory,
    Income,
    IncomeCategory,
    Invoice,
    Transfer,
)

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
        account_incomes = Income.query.filter_by(account_id=account.id).all()
        account_expenses = Expense.query.filter_by(account_id=account.id).all()

        opening_balance = (
            account.opening_balance
            if account.opening_balance is not None
            else Decimal("0")
        )
        in_transfer_amount = sum((t.amount for t in incoming), Decimal("0"))
        out_transfer_amount = sum((t.amount for t in outgoing), Decimal("0"))
        income_amount = sum((inc.amount for inc in account_incomes), Decimal("0"))
        expense_amount = sum((exp.amount for exp in account_expenses), Decimal("0"))
        original_balance = (
            opening_balance
            + in_transfer_amount
            - out_transfer_amount
            + income_amount
            - expense_amount
        )

        in_transfer_twd = sum(
            (t.amount * t.fx_rate_to_twd for t in incoming), Decimal("0")
        )
        out_transfer_twd = sum(
            (t.amount * t.fx_rate_to_twd for t in outgoing), Decimal("0")
        )
        income_twd = sum(
            (inc.amount * inc.fx_rate_to_twd for inc in account_incomes), Decimal("0")
        )
        expense_twd = sum(
            (exp.amount * exp.fx_rate_to_twd for exp in account_expenses), Decimal("0")
        )

        if account.currency == "TWD":
            twd_balance = (
                opening_balance
                + in_transfer_twd
                - out_transfer_twd
                + income_twd
                - expense_twd
            )
        else:
            twd_balance = (
                in_transfer_twd
                - out_transfer_twd
                + income_twd
                - expense_twd
            )

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


# ==========================================
# 入帳分類相關路由 (Income Categories)
# ==========================================


@api_bp.get("/income-categories")
def get_income_categories():
    try:
        categories = IncomeCategory.query.order_by(IncomeCategory.id.asc()).all()
        return json_response(
            200, "成功取得入帳分類清單", [c.to_dict() for c in categories]
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/income-categories")
def create_income_category():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填驗證：name
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return json_response(400, "缺少必填欄位或欄位為空：name（分類名稱）", None)
    name = name.strip()
    if len(name) > 50:
        return json_response(400, "分類名稱長度不可超過 50 個字元", None)

    try:
        existing = IncomeCategory.query.filter_by(name=name).first()
        if existing:
            return json_response(400, "入帳分類名稱已存在，請使用其他名稱", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    raw_description = payload.get("description")
    description = (
        str(raw_description).strip()
        if raw_description is not None and str(raw_description).strip()
        else None
    )

    category = IncomeCategory(
        name=name,
        description=description,
    )

    try:
        db.session.add(category)
        db.session.commit()
        return json_response(201, "入帳分類新增成功", category.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "分類資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/income-categories/<int:category_id>")
def get_income_category(category_id):
    try:
        category = db.session.get(IncomeCategory, category_id)
        if not category:
            return json_response(404, "找不到指定的入帳分類", None)
        return json_response(200, "成功取得入帳分類資料", category.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/income-categories/<int:category_id>")
def update_income_category(category_id):
    try:
        category = db.session.get(IncomeCategory, category_id)
        if not category:
            return json_response(404, "找不到指定的入帳分類", None)

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
                return json_response(400, "分類名稱不可為空", None)
            new_name = new_name.strip()
            if len(new_name) > 50:
                return json_response(400, "分類名稱長度不可超過 50 個字元", None)
            dup = IncomeCategory.query.filter(
                IncomeCategory.name == new_name, IncomeCategory.id != category_id
            ).first()
            if dup:
                return json_response(400, "入帳分類名稱已存在，請使用其他名稱", None)
            category.name = new_name

        if "description" in payload:
            raw_desc = payload["description"]
            if raw_desc is None:
                category.description = None
            else:
                category.description = str(raw_desc).strip() or None

        category.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "入帳分類更新成功", category.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 入帳明細相關路由 (Incomes)
# ==========================================


@api_bp.get("/incomes")
def get_incomes():
    try:
        query = Income.query

        # 查詢參數：account_id
        raw_account_id = request.args.get("account_id")
        if raw_account_id is not None:
            try:
                acc_id = int(raw_account_id)
                query = query.filter(Income.account_id == acc_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 account_id 必須為整數", None)

        # 查詢參數：category_id
        raw_category_id = request.args.get("category_id")
        if raw_category_id is not None:
            try:
                cat_id = int(raw_category_id)
                query = query.filter(Income.category_id == cat_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 category_id 必須為整數", None)

        # 查詢參數：date_from
        raw_date_from = request.args.get("date_from")
        if raw_date_from is not None:
            try:
                date_from = date.fromisoformat(raw_date_from.strip())
                query = query.filter(Income.received_on >= date_from)
            except (ValueError, TypeError):
                return json_response(
                    400, "查詢參數 date_from 格式錯誤，請使用 YYYY-MM-DD 格式", None
                )

        # 查詢參數：date_to
        raw_date_to = request.args.get("date_to")
        if raw_date_to is not None:
            try:
                date_to = date.fromisoformat(raw_date_to.strip())
                query = query.filter(Income.received_on <= date_to)
            except (ValueError, TypeError):
                return json_response(
                    400, "查詢參數 date_to 格式錯誤，請使用 YYYY-MM-DD 格式", None
                )

        incomes = query.order_by(Income.received_on.asc(), Income.id.asc()).all()
        return json_response(200, "成功取得入帳清單", [inc.to_dict() for inc in incomes])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/incomes")
def create_income():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：received_on（日期）
    raw_date = payload.get("received_on")
    if not raw_date:
        return json_response(400, "缺少必填欄位：received_on（入帳日期）", None)
    try:
        received_on = date.fromisoformat(str(raw_date).strip())
    except (ValueError, TypeError):
        return json_response(400, "入帳日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 必填：amount（金額 > 0）
    raw_amount = payload.get("amount")
    if raw_amount is None:
        return json_response(400, "缺少必填欄位：amount（入帳金額）", None)
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "入帳金額格式不正確，必須為數值", None)
    if amount <= 0:
        return json_response(400, "入帳金額必須大於 0", None)

    # 必填：currency（3 碼）
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

    # 必填：fx_rate_to_twd（匯率 > 0）
    raw_fx = payload.get("fx_rate_to_twd")
    if raw_fx is None:
        return json_response(400, "缺少必填欄位：fx_rate_to_twd（台幣匯率）", None)
    try:
        fx_rate_to_twd = Decimal(str(raw_fx).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "台幣匯率格式不正確，必須為數值", None)
    if fx_rate_to_twd <= 0:
        return json_response(400, "台幣匯率必須大於 0", None)

    # 必填：category_id
    raw_cat_id = payload.get("category_id")
    if raw_cat_id is None:
        return json_response(400, "缺少必填欄位：category_id（入帳分類）", None)
    try:
        category_id = int(raw_cat_id)
    except (ValueError, TypeError):
        return json_response(400, "入帳分類 ID 必須為整數", None)

    # 必填：account_id
    raw_acc_id = payload.get("account_id")
    if raw_acc_id is None:
        return json_response(400, "缺少必填欄位：account_id（入帳帳戶）", None)
    try:
        account_id = int(raw_acc_id)
    except (ValueError, TypeError):
        return json_response(400, "入帳帳戶 ID 必須為整數", None)

    # 檢查 source 與 note 至少一個有內容
    raw_source = payload.get("source")
    source = (
        str(raw_source).strip()
        if raw_source is not None and str(raw_source).strip()
        else None
    )

    raw_note = payload.get("note")
    note = (
        str(raw_note).strip()
        if raw_note is not None and str(raw_note).strip()
        else None
    )

    if not source and not note:
        return json_response(400, "來源（source）與備註（note）至少需填寫其中一項", None)

    # 檢查指向的帳戶與分類是否存在
    try:
        cat = db.session.get(IncomeCategory, category_id)
        if not cat:
            return json_response(400, "指定的入帳分類不存在", None)
        acc = db.session.get(Account, account_id)
        if not acc:
            return json_response(400, "指定的入帳帳戶不存在", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    # 可選 invoice_id（若客戶端提供）
    raw_invoice_id = payload.get("invoice_id")
    invoice_id = None
    if raw_invoice_id is not None:
        try:
            invoice_id = int(raw_invoice_id)
        except (ValueError, TypeError):
            return json_response(400, "發票 ID 格式錯誤，必須為整數", None)

    income = Income(
        received_on=received_on,
        amount=amount,
        currency=currency,
        fx_rate_to_twd=fx_rate_to_twd,
        category_id=category_id,
        account_id=account_id,
        source=source,
        note=note,
        invoice_id=invoice_id,
    )

    try:
        db.session.add(income)
        db.session.commit()
        return json_response(201, "入帳明細新增成功", income.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(
            400, "入帳資料違反約束條件（例如分類或帳戶不存在、來源與備註均為空）", None
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/incomes/<int:income_id>")
def get_income(income_id):
    try:
        income = db.session.get(Income, income_id)
        if not income:
            return json_response(404, "找不到指定的入帳明細", None)
        return json_response(200, "成功取得入帳資料", income.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/incomes/<int:income_id>")
def update_income(income_id):
    try:
        income = db.session.get(Income, income_id)
        if not income:
            return json_response(404, "找不到指定的入帳明細", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 不可修改主鍵與 invoice_id，直接忽略或拋棄
        # 修改日期 received_on
        if "received_on" in payload:
            raw_date = payload["received_on"]
            if not raw_date:
                return json_response(400, "入帳日期不可為空", None)
            try:
                income.received_on = date.fromisoformat(str(raw_date).strip())
            except (ValueError, TypeError):
                return json_response(400, "入帳日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

        # 修改金額 amount
        if "amount" in payload:
            raw_amount = payload["amount"]
            if raw_amount is None:
                return json_response(400, "入帳金額不可為空", None)
            try:
                amt = Decimal(str(raw_amount).strip())
                if amt <= 0:
                    return json_response(400, "入帳金額必須大於 0", None)
                income.amount = amt
            except (InvalidOperation, ValueError, TypeError):
                return json_response(400, "入帳金額格式不正確，必須為數值", None)

        # 修改幣別 currency
        if "currency" in payload:
            raw_currency = payload["currency"]
            if (
                not raw_currency
                or not isinstance(raw_currency, str)
                or len(raw_currency.strip()) != 3
            ):
                return json_response(400, "幣別代碼必須為 3 碼英文字母", None)
            income.currency = raw_currency.strip().upper()

        # 修改匯率 fx_rate_to_twd
        if "fx_rate_to_twd" in payload:
            raw_fx = payload["fx_rate_to_twd"]
            if raw_fx is None:
                return json_response(400, "台幣匯率不可為空", None)
            try:
                fx = Decimal(str(raw_fx).strip())
                if fx <= 0:
                    return json_response(400, "台幣匯率必須大於 0", None)
                income.fx_rate_to_twd = fx
            except (InvalidOperation, ValueError, TypeError):
                return json_response(400, "台幣匯率格式不正確，必須為數值", None)

        # 修改分類 category_id
        if "category_id" in payload:
            raw_cat_id = payload["category_id"]
            if raw_cat_id is None:
                return json_response(400, "入帳分類不可為空", None)
            try:
                cat_id = int(raw_cat_id)
                cat = db.session.get(IncomeCategory, cat_id)
                if not cat:
                    return json_response(400, "指定的入帳分類不存在", None)
                income.category_id = cat_id
            except (ValueError, TypeError):
                return json_response(400, "入帳分類 ID 必須為整數", None)

        # 修改帳戶 account_id
        if "account_id" in payload:
            raw_acc_id = payload["account_id"]
            if raw_acc_id is None:
                return json_response(400, "入帳帳戶不可為空", None)
            try:
                acc_id = int(raw_acc_id)
                acc = db.session.get(Account, acc_id)
                if not acc:
                    return json_response(400, "指定的入帳帳戶不存在", None)
                income.account_id = acc_id
            except (ValueError, TypeError):
                return json_response(400, "入帳帳戶 ID 必須為整數", None)

        # 檢驗更新後的 source 與 note 是否維持至少一項非空
        new_source = income.source
        if "source" in payload:
            raw_src = payload["source"]
            new_source = (
                str(raw_src).strip()
                if raw_src is not None and str(raw_src).strip()
                else None
            )

        new_note = income.note
        if "note" in payload:
            raw_nt = payload["note"]
            new_note = (
                str(raw_nt).strip()
                if raw_nt is not None and str(raw_nt).strip()
                else None
            )

        if not new_source and not new_note:
            return json_response(400, "來源（source）與備註（note）不可同時為空", None)

        income.source = new_source
        income.note = new_note
        income.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "入帳明細更新成功", income.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 花費分類相關路由 (Expense Categories)
# ==========================================


@api_bp.get("/expense-categories")
def get_expense_categories():
    try:
        categories = ExpenseCategory.query.order_by(ExpenseCategory.id.asc()).all()
        return json_response(
            200, "成功取得花費分類清單", [c.to_dict() for c in categories]
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/expense-categories")
def create_expense_category():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填驗證：name
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return json_response(400, "缺少必填欄位或欄位為空：name（分類名稱）", None)
    name = name.strip()
    if len(name) > 50:
        return json_response(400, "分類名稱長度不可超過 50 個字元", None)

    try:
        existing = ExpenseCategory.query.filter_by(name=name).first()
        if existing:
            return json_response(400, "花費分類名稱已存在，請使用其他名稱", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    raw_description = payload.get("description")
    description = (
        str(raw_description).strip()
        if raw_description is not None and str(raw_description).strip()
        else None
    )

    category = ExpenseCategory(
        name=name,
        description=description,
    )

    try:
        db.session.add(category)
        db.session.commit()
        return json_response(201, "花費分類新增成功", category.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "分類資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/expense-categories/<int:category_id>")
def get_expense_category(category_id):
    try:
        category = db.session.get(ExpenseCategory, category_id)
        if not category:
            return json_response(404, "找不到指定的花費分類", None)
        return json_response(200, "成功取得花費分類資料", category.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/expense-categories/<int:category_id>")
def update_expense_category(category_id):
    try:
        category = db.session.get(ExpenseCategory, category_id)
        if not category:
            return json_response(404, "找不到指定的花費分類", None)

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
                return json_response(400, "分類名稱不可為空", None)
            new_name = new_name.strip()
            if len(new_name) > 50:
                return json_response(400, "分類名稱長度不可超過 50 個字元", None)
            dup = ExpenseCategory.query.filter(
                ExpenseCategory.name == new_name, ExpenseCategory.id != category_id
            ).first()
            if dup:
                return json_response(400, "花費分類名稱已存在，請使用其他名稱", None)
            category.name = new_name

        if "description" in payload:
            raw_desc = payload["description"]
            if raw_desc is None:
                category.description = None
            else:
                category.description = str(raw_desc).strip() or None

        category.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "花費分類更新成功", category.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件（例如名稱重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 花費明細相關路由 (Expenses)
# ==========================================


@api_bp.get("/expenses")
def get_expenses():
    try:
        query = Expense.query

        # 查詢參數：account_id
        raw_account_id = request.args.get("account_id")
        if raw_account_id is not None:
            try:
                acc_id = int(raw_account_id)
                query = query.filter(Expense.account_id == acc_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 account_id 必須為整數", None)

        # 查詢參數：category_id
        raw_category_id = request.args.get("category_id")
        if raw_category_id is not None:
            try:
                cat_id = int(raw_category_id)
                query = query.filter(Expense.category_id == cat_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 category_id 必須為整數", None)

        # 查詢參數：invoice_id
        raw_invoice_id = request.args.get("invoice_id")
        if raw_invoice_id is not None:
            try:
                inv_id = int(raw_invoice_id)
                query = query.filter(Expense.invoice_id == inv_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 invoice_id 必須為整數", None)

        # 查詢參數：date_from
        raw_date_from = request.args.get("date_from")
        if raw_date_from is not None:
            try:
                date_from = date.fromisoformat(raw_date_from.strip())
                query = query.filter(Expense.spent_on >= date_from)
            except (ValueError, TypeError):
                return json_response(
                    400, "查詢參數 date_from 格式錯誤，請使用 YYYY-MM-DD 格式", None
                )

        # 查詢參數：date_to
        raw_date_to = request.args.get("date_to")
        if raw_date_to is not None:
            try:
                date_to = date.fromisoformat(raw_date_to.strip())
                query = query.filter(Expense.spent_on <= date_to)
            except (ValueError, TypeError):
                return json_response(
                    400, "查詢參數 date_to 格式錯誤，請使用 YYYY-MM-DD 格式", None
                )

        expenses = query.order_by(Expense.spent_on.asc(), Expense.id.asc()).all()
        return json_response(200, "成功取得花費清單", [exp.to_dict() for exp in expenses])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/expenses")
def create_expense():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：spent_on（消費日）
    raw_spent_on = payload.get("spent_on")
    if not raw_spent_on:
        return json_response(400, "缺少必填欄位：spent_on（消費日期）", None)
    try:
        spent_on = date.fromisoformat(str(raw_spent_on).strip())
    except (ValueError, TypeError):
        return json_response(400, "消費日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 選填：settled_on（扣款/結算日）
    raw_settled_on = payload.get("settled_on")
    settled_on = None
    if raw_settled_on is not None and str(raw_settled_on).strip():
        try:
            settled_on = date.fromisoformat(str(raw_settled_on).strip())
        except (ValueError, TypeError):
            return json_response(400, "結算日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 必填：amount（金額 > 0）
    raw_amount = payload.get("amount")
    if raw_amount is None:
        return json_response(400, "缺少必填欄位：amount（花費金額）", None)
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "花費金額格式不正確，必須為數值", None)
    if amount <= 0:
        return json_response(400, "花費金額必須大於 0", None)

    # 必填：currency（3 碼）
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

    # 必填：fx_rate_to_twd（匯率 > 0）
    raw_fx = payload.get("fx_rate_to_twd")
    if raw_fx is None:
        return json_response(400, "缺少必填欄位：fx_rate_to_twd（台幣匯率）", None)
    try:
        fx_rate_to_twd = Decimal(str(raw_fx).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "台幣匯率格式不正確，必須為數值", None)
    if fx_rate_to_twd <= 0:
        return json_response(400, "台幣匯率必須大於 0", None)

    # 必填：category_id
    raw_cat_id = payload.get("category_id")
    if raw_cat_id is None:
        return json_response(400, "缺少必填欄位：category_id（花費分類）", None)
    try:
        category_id = int(raw_cat_id)
    except (ValueError, TypeError):
        return json_response(400, "花費分類 ID 必須為整數", None)

    # 必填：account_id
    raw_acc_id = payload.get("account_id")
    if raw_acc_id is None:
        return json_response(400, "缺少必填欄位：account_id（支付帳戶）", None)
    try:
        account_id = int(raw_acc_id)
    except (ValueError, TypeError):
        return json_response(400, "支付帳戶 ID 必須為整數", None)

    # 檢查 merchant 與 note 至少一個有內容
    raw_merchant = payload.get("merchant")
    merchant = (
        str(raw_merchant).strip()
        if raw_merchant is not None and str(raw_merchant).strip()
        else None
    )

    raw_note = payload.get("note")
    note = (
        str(raw_note).strip()
        if raw_note is not None and str(raw_note).strip()
        else None
    )

    if not merchant and not note:
        return json_response(400, "商家（merchant）與備註（note）至少需填寫其中一項", None)

    # 檢查指向的帳戶、分類與發票是否存在
    try:
        cat = db.session.get(ExpenseCategory, category_id)
        if not cat:
            return json_response(400, "指派的花費分類不存在", None)
        acc = db.session.get(Account, account_id)
        if not acc:
            return json_response(400, "指派的支付帳戶不存在", None)

        raw_invoice_id = payload.get("invoice_id")
        invoice_id = None
        if raw_invoice_id is not None and str(raw_invoice_id).strip():
            try:
                invoice_id = int(raw_invoice_id)
            except (ValueError, TypeError):
                return json_response(400, "發票 ID 格式錯誤，必須為整數", None)
            inv = db.session.get(Invoice, invoice_id)
            if not inv:
                return json_response(400, "指派的統一發票不存在", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    expense = Expense(
        spent_on=spent_on,
        settled_on=settled_on,
        amount=amount,
        currency=currency,
        fx_rate_to_twd=fx_rate_to_twd,
        category_id=category_id,
        account_id=account_id,
        merchant=merchant,
        note=note,
        invoice_id=invoice_id,
    )

    try:
        db.session.add(expense)
        db.session.commit()
        return json_response(201, "花費記錄新增成功", expense.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(
            400, "花費資料違反約束條件（例如分類、帳戶或發票不存在、商家與備註均為空）", None
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/expenses/<int:expense_id>")
def get_expense(expense_id):
    try:
        expense = db.session.get(Expense, expense_id)
        if not expense:
            return json_response(404, "找不到指定的花費記錄", None)
        return json_response(200, "成功取得花費資料", expense.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/expenses/<int:expense_id>")
def update_expense(expense_id):
    try:
        expense = db.session.get(Expense, expense_id)
        if not expense:
            return json_response(404, "找不到指定的花費記錄", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 不可修改主鍵，直接忽略

        # 修改消費日 spent_on
        if "spent_on" in payload:
            raw_spent = payload["spent_on"]
            if not raw_spent:
                return json_response(400, "消費日期不可為空", None)
            try:
                expense.spent_on = date.fromisoformat(str(raw_spent).strip())
            except (ValueError, TypeError):
                return json_response(400, "消費日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

        # 修改結算日 settled_on
        if "settled_on" in payload:
            raw_settled = payload["settled_on"]
            if raw_settled is None or not str(raw_settled).strip():
                expense.settled_on = None
            else:
                try:
                    expense.settled_on = date.fromisoformat(str(raw_settled).strip())
                except (ValueError, TypeError):
                    return json_response(400, "結算日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

        # 修改金額 amount
        if "amount" in payload:
            raw_amount = payload["amount"]
            if raw_amount is None:
                return json_response(400, "花費金額不可為空", None)
            try:
                amt = Decimal(str(raw_amount).strip())
                if amt <= 0:
                    return json_response(400, "花費金額必須大於 0", None)
                expense.amount = amt
            except (InvalidOperation, ValueError, TypeError):
                return json_response(400, "花費金額格式不正確，必須為數值", None)

        # 修改幣別 currency
        if "currency" in payload:
            raw_currency = payload["currency"]
            if (
                not raw_currency
                or not isinstance(raw_currency, str)
                or len(raw_currency.strip()) != 3
            ):
                return json_response(400, "幣別代碼必須為 3 碼英文字母", None)
            expense.currency = raw_currency.strip().upper()

        # 修改匯率 fx_rate_to_twd
        if "fx_rate_to_twd" in payload:
            raw_fx = payload["fx_rate_to_twd"]
            if raw_fx is None:
                return json_response(400, "台幣匯率不可為空", None)
            try:
                fx = Decimal(str(raw_fx).strip())
                if fx <= 0:
                    return json_response(400, "台幣匯率必須大於 0", None)
                expense.fx_rate_to_twd = fx
            except (InvalidOperation, ValueError, TypeError):
                return json_response(400, "台幣匯率格式不正確，必須為數值", None)

        # 修改分類 category_id
        if "category_id" in payload:
            raw_cat_id = payload["category_id"]
            if raw_cat_id is None:
                return json_response(400, "花費分類不可為空", None)
            try:
                cat_id = int(raw_cat_id)
                cat = db.session.get(ExpenseCategory, cat_id)
                if not cat:
                    return json_response(400, "指派的花費分類不存在", None)
                expense.category_id = cat_id
            except (ValueError, TypeError):
                return json_response(400, "花費分類 ID 必須為整數", None)

        # 修改帳戶 account_id
        if "account_id" in payload:
            raw_acc_id = payload["account_id"]
            if raw_acc_id is None:
                return json_response(400, "支付帳戶不可為空", None)
            try:
                acc_id = int(raw_acc_id)
                acc = db.session.get(Account, acc_id)
                if not acc:
                    return json_response(400, "指派的支付帳戶不存在", None)
                expense.account_id = acc_id
            except (ValueError, TypeError):
                return json_response(400, "支付帳戶 ID 必須為整數", None)

        # 修改發票 invoice_id
        if "invoice_id" in payload:
            raw_inv_id = payload["invoice_id"]
            if raw_inv_id is None or not str(raw_inv_id).strip():
                expense.invoice_id = None
            else:
                try:
                    inv_id = int(raw_inv_id)
                    inv = db.session.get(Invoice, inv_id)
                    if not inv:
                        return json_response(400, "指派的統一發票不存在", None)
                    expense.invoice_id = inv_id
                except (ValueError, TypeError):
                    return json_response(400, "發票 ID 格式錯誤，必須為整數", None)

        # 檢驗更新後的 merchant 與 note 是否維持至少一項非空
        new_merchant = expense.merchant
        if "merchant" in payload:
            raw_m = payload["merchant"]
            new_merchant = (
                str(raw_m).strip()
                if raw_m is not None and str(raw_m).strip()
                else None
            )

        new_note = expense.note
        if "note" in payload:
            raw_n = payload["note"]
            new_note = (
                str(raw_n).strip()
                if raw_n is not None and str(raw_n).strip()
                else None
            )

        if not new_merchant and not new_note:
            return json_response(400, "商家（merchant）與備註（note）不可同時為空", None)

        expense.merchant = new_merchant
        expense.note = new_note
        expense.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "花費記錄更新成功", expense.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

