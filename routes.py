from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Account,
    DrawPeriod,
    DrawPrize,
    Expense,
    ExpenseCategory,
    Income,
    IncomeCategory,
    Instrument,
    Invoice,
    InvoiceItem,
    Trade,
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


def compute_period_key(d: date) -> str:
    """依開立日推導發票期別：1-2月為 YYYY-01，3-4月為 YYYY-03，...，11-12月為 YYYY-11"""
    odd_month = ((d.month - 1) // 2) * 2 + 1
    return f"{d.year:04d}-{odd_month:02d}"


# ==========================================
# 月度現金流彙總路由 (Monthly Summary)
# ==========================================


@api_bp.get("/summary")
def get_monthly_summary():
    raw_month = request.args.get("month")
    if not raw_month or not isinstance(raw_month, str) or not raw_month.strip():
        return json_response(400, "缺少必填查詢參數：month（月份，格式 YYYY-MM）", None)

    month_str = raw_month.strip()
    import re

    match = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", month_str)
    if not match:
        return json_response(400, "查詢參數 month 格式不符，請使用 YYYY-MM 格式（如 2026-11）", None)

    year = int(match.group(1))
    month = int(match.group(2))

    start_date = date(year, month, 1)
    if month in {1, 3, 5, 7, 8, 10, 12}:
        last_day = 31
    elif month in {4, 6, 9, 11}:
        last_day = 30
    else:
        last_day = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    end_date = date(year, month, last_day)

    try:
        # 入帳合計：received_on 落在該月的入帳，金額乘 fx_rate_to_twd 後加總
        incomes = Income.query.filter(
            Income.received_on >= start_date, Income.received_on <= end_date
        ).all()
        income_twd = sum(
            (inc.amount * inc.fx_rate_to_twd for inc in incomes), Decimal("0")
        )

        # 花費合計：spent_on 落在該月的花費，金額乘 fx_rate_to_twd 後加總
        expenses = Expense.query.filter(
            Expense.spent_on >= start_date, Expense.spent_on <= end_date
        ).all()
        expense_twd = sum(
            (exp.amount * exp.fx_rate_to_twd for exp in expenses), Decimal("0")
        )

        data = {
            "month": month_str,
            "income_twd": float(income_twd),
            "expense_twd": float(expense_twd),
        }
        return json_response(200, "成功取得月份現金流彙總", data)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


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
        account_trades = Trade.query.filter(
            Trade.account_id == account.id, Trade.voided_at.is_(None)
        ).all()

        opening_balance = (
            account.opening_balance
            if account.opening_balance is not None
            else Decimal("0")
        )
        in_transfer_amount = sum((t.amount for t in incoming), Decimal("0"))
        out_transfer_amount = sum((t.amount for t in outgoing), Decimal("0"))
        income_amount = sum((inc.amount for inc in account_incomes), Decimal("0"))
        expense_amount = sum((exp.amount for exp in account_expenses), Decimal("0"))

        trade_amount = Decimal("0")
        trade_twd = Decimal("0")

        for tr in account_trades:
            qty = tr.quantity if tr.quantity is not None else Decimal("0")
            prc = tr.price if tr.price is not None else Decimal("0")
            fee = tr.fee if tr.fee is not None else Decimal("0")
            tax = tr.tax if tr.tax is not None else Decimal("0")
            cash = tr.cash_amount if tr.cash_amount is not None else Decimal("0")
            fx = tr.fx_rate_to_twd if tr.fx_rate_to_twd is not None else Decimal("1.0")

            if tr.trade_type == "buy":
                cost = (qty * prc) + fee + tax
                trade_amount -= cost
                trade_twd -= cost * fx
            elif tr.trade_type == "sell":
                proceeds = (qty * prc) - fee - tax
                trade_amount += proceeds
                trade_twd += proceeds * fx
            elif tr.trade_type in {"cash_dividend", "capital_reduction"}:
                trade_amount += cash
                trade_twd += cash * fx

        original_balance = (
            opening_balance
            + in_transfer_amount
            - out_transfer_amount
            + income_amount
            - expense_amount
            + trade_amount
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
                + trade_twd
            )
        else:
            twd_balance = (
                in_transfer_twd
                - out_transfer_twd
                + income_twd
                - expense_twd
                + trade_twd
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


# ==========================================
# 統一發票與品項相關路由 (Invoices & Items)
# ==========================================


@api_bp.post("/invoices")
def create_invoice():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：number（發票號碼：兩碼大寫英文加八碼數字）
    raw_number = payload.get("number")
    if not raw_number or not isinstance(raw_number, str):
        return json_response(400, "缺少必填欄位或格式錯誤：number（發票號碼）", None)
    number = raw_number.strip().upper()
    import re

    if not re.match(r"^[A-Z]{2}[0-9]{8}$", number):
        return json_response(
            400, "發票號碼格式不符，必須為 2 碼大寫英文字母加上 8 碼數字", None
        )

    # 必填：issued_on（開立日）
    raw_issued = payload.get("issued_on")
    if not raw_issued:
        return json_response(400, "缺少必填欄位：issued_on（開立日期）", None)
    try:
        issued_on = date.fromisoformat(str(raw_issued).strip())
    except (ValueError, TypeError):
        return json_response(400, "開立日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 必填：amount（金額 > 0）
    raw_amount = payload.get("amount")
    if raw_amount is None:
        return json_response(400, "缺少必填欄位：amount（發票金額）", None)
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "發票金額格式不正確，必須為數值", None)
    if amount <= 0:
        return json_response(400, "發票金額必須大於 0", None)

    # 必填：kind（只允許 paper、electronic）
    kind = payload.get("kind")
    if not kind or not isinstance(kind, str) or kind.strip() not in {"paper", "electronic"}:
        return json_response(400, "發票種類不合法，只允許 paper 或 electronic", None)
    kind = kind.strip()

    # 選填：currency（未送時用 TWD）
    raw_currency = payload.get("currency")
    if raw_currency is None or not str(raw_currency).strip():
        currency = "TWD"
    else:
        currency = str(raw_currency).strip().upper()
        if len(currency) != 3:
            return json_response(400, "幣別代碼必須為 3 碼英文字母", None)

    # 選填：fx_rate_to_twd（未送且幣別為 TWD 時用 1）
    raw_fx = payload.get("fx_rate_to_twd")
    if raw_fx is None or not str(raw_fx).strip():
        if currency == "TWD":
            fx_rate_to_twd = Decimal("1.000000")
        else:
            return json_response(400, "外幣發票必須填寫匯率 fx_rate_to_twd", None)
    else:
        try:
            fx_rate_to_twd = Decimal(str(raw_fx).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "台幣匯率格式不正確，必須為數值", None)
        if fx_rate_to_twd <= 0:
            return json_response(400, "台幣匯率必須大於 0", None)

    # 選填：random_code（可空，有值時為四碼數字）
    raw_random = payload.get("random_code")
    random_code = None
    if raw_random is not None and str(raw_random).strip():
        random_code = str(raw_random).strip()
        if not re.match(r"^[0-9]{4}$", random_code):
            return json_response(400, "隨機碼格式錯誤，必須為 4 碼數字", None)

    # 選填：seller_name, seller_ban
    raw_seller_name = payload.get("seller_name")
    seller_name = str(raw_seller_name).strip() if raw_seller_name else None

    raw_seller_ban = payload.get("seller_ban")
    seller_ban = str(raw_seller_ban).strip() if raw_seller_ban else None

    # 重複號碼檢查
    try:
        existing = Invoice.query.filter_by(number=number).first()
        if existing:
            return json_response(400, "此發票號碼已存在，不可重複建立", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)

    invoice = Invoice(
        number=number,
        issued_on=issued_on,
        amount=amount,
        kind=kind,
        currency=currency,
        fx_rate_to_twd=fx_rate_to_twd,
        random_code=random_code,
        seller_name=seller_name,
        seller_ban=seller_ban,
        prize_status="unchecked",
    )

    try:
        db.session.add(invoice)
        db.session.commit()
        return json_response(201, "發票建立成功", invoice.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "發票資料違反約束條件（例如號碼重複或格式錯誤）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/invoices")
def get_invoices():
    try:
        query = Invoice.query

        # 篩選：period_key (格式例如 2026-01)
        raw_period_key = request.args.get("period_key")
        if raw_period_key is not None and raw_period_key.strip():
            period_key = raw_period_key.strip()
            # 依開立日計算期別過濾：YYYY-MM 到 YYYY-MM+1
            import re

            match = re.match(r"^(\d{4})-(01|03|05|07|09|11)$", period_key)
            if not match:
                return json_response(
                    400,
                    "期別格式錯誤，必須為 YYYY-01、YYYY-03、YYYY-05、YYYY-07、YYYY-09 或 YYYY-11",
                    None,
                )
            year = int(match.group(1))
            start_month = int(match.group(2))
            start_date = date(year, start_month, 1)
            end_month = start_month + 1
            # 次月最後一日
            if end_month in {1, 3, 5, 7, 8, 10, 12}:
                last_day = 31
            elif end_month in {4, 6, 9, 11}:
                last_day = 30
            else:
                last_day = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
            end_date = date(year, end_month, last_day)

            query = query.filter(
                Invoice.issued_on >= start_date, Invoice.issued_on <= end_date
            )

        # 篩選：prize_status
        raw_status = request.args.get("prize_status")
        if raw_status is not None and raw_status.strip():
            status = raw_status.strip()
            if status not in {"unchecked", "lost", "won", "claimed", "void"}:
                return json_response(
                    400,
                    "中獎狀態不合法，只允許 unchecked、lost、won、claimed、void",
                    None,
                )
            query = query.filter(Invoice.prize_status == status)

        invoices = query.order_by(Invoice.issued_on.asc(), Invoice.id.asc()).all()
        return json_response(200, "成功取得發票清單", [inv.to_dict() for inv in invoices])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/invoices/<int:invoice_id>")
def get_invoice(invoice_id):
    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            return json_response(404, "找不到指定的發票", None)

        data = invoice.to_dict()
        # 帶上品項清單
        data["items"] = [item.to_dict() for item in invoice.items]
        # 帶上已掛上的花費 id 清單
        expenses = Expense.query.filter_by(invoice_id=invoice.id).all()
        data["expense_ids"] = [exp.id for exp in expenses]

        return json_response(200, "成功取得發票資料", data)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.patch("/invoices/<int:invoice_id>")
def update_invoice(invoice_id):
    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            return json_response(404, "找不到指定的發票", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 不可改號碼與主鍵，若有傳送直接忽略

        import re

        if "seller_name" in payload:
            raw_sname = payload["seller_name"]
            invoice.seller_name = str(raw_sname).strip() if raw_sname else None

        if "seller_ban" in payload:
            raw_sban = payload["seller_ban"]
            invoice.seller_ban = str(raw_sban).strip() if raw_sban else None

        if "random_code" in payload:
            raw_random = payload["random_code"]
            if raw_random is None or not str(raw_random).strip():
                invoice.random_code = None
            else:
                rc = str(raw_random).strip()
                if not re.match(r"^[0-9]{4}$", rc):
                    return json_response(400, "隨機碼格式錯誤，必須為 4 碼數字", None)
                invoice.random_code = rc

        if "prize_status" in payload:
            new_status = payload["prize_status"]
            if new_status == "void":
                invoice.prize_status = "void"
            elif new_status is not None:
                return json_response(
                    400, "透過此端點修改中獎狀態僅允許改為 void（作廢）", None
                )

        invoice.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "發票更新成功", invoice.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "更新資料違反約束條件", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/invoices/<int:invoice_id>/items")
def create_invoice_item(invoice_id):
    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            return json_response(404, "找不到指定的發票", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 必填：name
        raw_name = payload.get("name")
        if not raw_name or not isinstance(raw_name, str) or not raw_name.strip():
            return json_response(400, "缺少必填欄位或欄位為空：name（品項名稱）", None)
        name = raw_name.strip()

        # 必填：quantity > 0
        raw_qty = payload.get("quantity")
        if raw_qty is None:
            return json_response(400, "缺少必填欄位：quantity（數量）", None)
        try:
            quantity = Decimal(str(raw_qty).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "數量格式不正確，必須為數值", None)
        if quantity <= 0:
            return json_response(400, "數量必須大於 0", None)

        # 必填：unit_price > 0
        raw_price = payload.get("unit_price")
        if raw_price is None:
            return json_response(400, "缺少必填欄位：unit_price（單價）", None)
        try:
            unit_price = Decimal(str(raw_price).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "單價格式不正確，必須為數值", None)
        if unit_price <= 0:
            return json_response(400, "單價必須大於 0", None)

        # 必填：line_amount > 0
        raw_subtotal = payload.get("line_amount")
        if raw_subtotal is None:
            return json_response(400, "缺少必填欄位：line_amount（小計金額）", None)
        try:
            line_amount = Decimal(str(raw_subtotal).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "小計金額格式不正確，必須為數值", None)
        if line_amount <= 0:
            return json_response(400, "小計金額必須大於 0", None)

        item = InvoiceItem(
            invoice_id=invoice.id,
            name=name,
            quantity=quantity,
            unit_price=unit_price,
            line_amount=line_amount,
        )

        db.session.add(item)
        db.session.commit()
        return json_response(201, "發票品項新增成功", item.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "品項資料違反約束條件", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/invoices/<int:invoice_id>/items")
def get_invoice_items(invoice_id):
    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            return json_response(404, "找不到指定的發票", None)

        items = InvoiceItem.query.filter_by(invoice_id=invoice.id).order_by(InvoiceItem.id.asc()).all()
        return json_response(200, "成功取得發票品項清單", [item.to_dict() for item in items])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 開獎期與獎號相關路由 (Draw Periods & Prizes)
# ==========================================


@api_bp.post("/draw-periods")
def create_draw_period():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    raw_key = payload.get("period_key")
    if not raw_key or not isinstance(raw_key, str) or not raw_key.strip():
        return json_response(400, "缺少必填欄位或欄位為空：period_key（期別）", None)
    period_key = raw_key.strip()

    import re

    if not re.match(r"^\d{4}-(01|03|05|07|09|11)$", period_key):
        return json_response(
            400,
            "期別格式不符，請使用 YYYY-01、YYYY-03、YYYY-05、YYYY-07、YYYY-09 或 YYYY-11 格式",
            None,
        )

    try:
        existing = DrawPeriod.query.filter_by(period_key=period_key).first()
        if existing:
            return json_response(400, "此開獎期別已存在，不可重複建立", None)

        period = DrawPeriod(period_key=period_key)
        db.session.add(period)
        db.session.commit()
        return json_response(201, "開獎期建立成功", period.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "開獎期資料違反約束條件（例如期別重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/draw-periods/<int:period_id>/prizes")
def create_draw_prize(period_id):
    try:
        period = db.session.get(DrawPeriod, period_id)
        if not period:
            return json_response(404, "找不到指定的開獎期", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 必填：tier
        raw_tier = payload.get("tier")
        allowed_tiers = {
            "special",
            "grand",
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "extra_sixth",
        }
        if not raw_tier or raw_tier.strip() not in allowed_tiers:
            return json_response(
                400,
                "獎項種類不合法，只允許 special、grand、first、second、third、fourth、fifth、sixth、extra_sixth",
                None,
            )
        tier = raw_tier.strip()

        # 必填：matching_number
        raw_num = payload.get("matching_number")
        if not raw_num or not isinstance(raw_num, str) or not raw_num.strip():
            return json_response(400, "缺少必填欄位或欄位為空：matching_number（對獎號碼）", None)
        matching_number = raw_num.strip()

        import re

        if tier in {"special", "grand", "first"}:
            if not re.match(r"^[0-9]{8}$", matching_number):
                return json_response(
                    400, f"{tier} 獎項的對獎號碼必須是嚴格 8 碼數字", None
                )
        elif not re.match(r"^[0-9]+$", matching_number):
            return json_response(400, "對獎號碼必須為純數字", None)

        # 必填：amount >= 0
        raw_amount = payload.get("amount")
        if raw_amount is None:
            return json_response(400, "缺少必填欄位：amount（獎金金額）", None)
        try:
            amount = Decimal(str(raw_amount).strip())
        except (InvalidOperation, ValueError, TypeError):
            return json_response(400, "獎金金額格式不正確，必須為數值", None)
        if amount < 0:
            return json_response(400, "獎金金額不可小於 0", None)

        prize = DrawPrize(
            draw_period_id=period.id,
            tier=tier,
            matching_number=matching_number,
            amount=amount,
        )

        db.session.add(prize)
        db.session.commit()
        return json_response(201, "獎項新增成功", prize.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(
            400, "同一期、同一獎項且同一中獎號碼不可重複建立", None
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/draw-periods/<int:period_id>/check")
def check_period_invoices(period_id):
    try:
        period = db.session.get(DrawPeriod, period_id)
        if not period:
            return json_response(404, "找不到指定的開獎期", None)

        # 取得該開獎期底下的所有獎項
        prizes = DrawPrize.query.filter_by(draw_period_id=period.id).all()
        if not prizes:
            return json_response(400, "該期尚未設定任何獎項與獎號，無法執行對獎", None)

        # 計算該期開立日範圍
        import re

        match = re.match(r"^(\d{4})-(01|03|05|07|09|11)$", period.period_key)
        if not match:
            return json_response(400, "期別格式錯誤，無法計算開立日範圍", None)
        year = int(match.group(1))
        start_month = int(match.group(2))
        start_date = date(year, start_month, 1)
        end_month = start_month + 1
        if end_month in {1, 3, 5, 7, 8, 10, 12}:
            last_day = 31
        elif end_month in {4, 6, 9, 11}:
            last_day = 30
        else:
            last_day = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
        end_date = date(year, end_month, last_day)

        # 找出所有開立日落在該期，且未作廢（prize_status != 'void'）的發票
        invoices = (
            Invoice.query.filter(
                Invoice.issued_on >= start_date,
                Invoice.issued_on <= end_date,
                Invoice.prize_status != "void",
            )
            .all()
        )

        checked_count = len(invoices)
        won_count = 0

        # 將該期的開獎獎項依照獎項規則分類
        special_prizes = [p for p in prizes if p.tier == "special"]
        grand_prizes = [p for p in prizes if p.tier == "grand"]
        first_prizes = [p for p in prizes if p.tier == "first"]
        # 頭獎衍生獎項設定（依資料庫中的定義）：second(末7), third(末6), fourth(末5), fifth(末4), sixth(末3)
        other_prizes = [p for p in prizes if p.tier not in {"special", "grand", "first"}]

        for inv in invoices:
            # 去掉發票號碼前兩碼英文，取後 8 碼數字
            inv_num = inv.number[2:]
            won = False

            # 1. 對特別獎（同期 8 碼全中）
            if any(p.matching_number == inv_num for p in special_prizes):
                won = True

            # 2. 對特獎（同期 8 碼全中）
            if not won and any(p.matching_number == inv_num for p in grand_prizes):
                won = True

            # 3. 對頭獎與其衍生二~六獎
            if not won and first_prizes:
                for fp in first_prizes:
                    fn = fp.matching_number
                    if inv_num == fn:
                        won = True
                        break
                    elif inv_num[-7:] == fn[-7:] and any(p.tier == "second" for p in prizes):
                        won = True
                        break
                    elif inv_num[-6:] == fn[-6:] and any(p.tier == "third" for p in prizes):
                        won = True
                        break
                    elif inv_num[-5:] == fn[-5:] and any(p.tier == "fourth" for p in prizes):
                        won = True
                        break
                    elif inv_num[-4:] == fn[-4:] and any(p.tier == "fifth" for p in prizes):
                        won = True
                        break
                    elif inv_num[-3:] == fn[-3:] and any(p.tier == "sixth" for p in prizes):
                        won = True
                        break

            # 4. 對其他獨立獎項（例如增開六獎 extra_sixth 末 3 碼，或獨立設定之 sixth）
            if not won and other_prizes:
                for op in other_prizes:
                    m_len = len(op.matching_number)
                    if inv_num[-m_len:] == op.matching_number:
                        won = True
                        break

            # 依對獎結果更新發票狀態（已兌領 claimed 保持 claimed；否則依中獎更新為 won 或 lost）
            if won:
                won_count += 1
                if inv.prize_status != "claimed":
                    inv.prize_status = "won"
            else:
                if inv.prize_status != "claimed":
                    inv.prize_status = "lost"

            inv.updated_at = datetime.now(timezone.utc)

        db.session.commit()
        return json_response(
            200,
            "對獎完成",
            {
                "period_id": period.id,
                "period_key": period.period_key,
                "checked_count": checked_count,
                "won_count": won_count,
            },
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/invoices/<int:invoice_id>/prize-income")
def claim_invoice_prize(invoice_id):
    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            return json_response(404, "找不到指定的發票", None)

        # 狀態檢查：僅 prize_status 為 won 且尚未有入帳時可呼叫
        if invoice.prize_status != "won":
            return json_response(
                400,
                f"該發票中獎狀態為 {invoice.prize_status}，只有狀態為 won（已中獎）之發票才可領取獎金",
                None,
            )

        # 檢查是否已存在對應此 invoice_id 的入帳
        existing_income = Income.query.filter_by(invoice_id=invoice.id).first()
        if existing_income:
            return json_response(400, "此發票已領取過獎金，不可重複領取", None)

        if not request.is_json:
            return json_response(400, "請使用 JSON 格式傳送請求", None)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return json_response(400, "請求內容必須為 JSON 物件", None)

        # 必填：category_id 與 account_id
        raw_cat_id = payload.get("category_id")
        if raw_cat_id is None:
            return json_response(400, "缺少必填欄位：category_id（入帳分類）", None)
        try:
            category_id = int(raw_cat_id)
            cat = db.session.get(IncomeCategory, category_id)
            if not cat:
                return json_response(400, "指定的入帳分類不存在", None)
        except (ValueError, TypeError):
            return json_response(400, "入帳分類 ID 必須為整數", None)

        raw_acc_id = payload.get("account_id")
        if raw_acc_id is None:
            return json_response(400, "缺少必填欄位：account_id（入帳帳戶）", None)
        try:
            account_id = int(raw_acc_id)
            acc = db.session.get(Account, account_id)
            if not acc:
                return json_response(400, "指定的入帳帳戶不存在", None)
        except (ValueError, TypeError):
            return json_response(400, "入帳帳戶 ID 必須為整數", None)

        # 計算對到的獎金金額：從開獎期別與獎號表查找
        period_key = compute_period_key(invoice.issued_on)
        period = DrawPeriod.query.filter_by(period_key=period_key).first()
        if not period:
            return json_response(400, "找不到該發票開立期別之開獎資料", None)

        prizes = DrawPrize.query.filter_by(draw_period_id=period.id).all()
        inv_num = invoice.number[2:]

        matched_amount = None

        special_prizes = [p for p in prizes if p.tier == "special"]
        grand_prizes = [p for p in prizes if p.tier == "grand"]
        first_prizes = [p for p in prizes if p.tier == "first"]
        second_prizes = [p for p in prizes if p.tier == "second"]
        third_prizes = [p for p in prizes if p.tier == "third"]
        fourth_prizes = [p for p in prizes if p.tier == "fourth"]
        fifth_prizes = [p for p in prizes if p.tier == "fifth"]
        sixth_prizes = [p for p in prizes if p.tier == "sixth"]
        extra_sixth_prizes = [p for p in prizes if p.tier == "extra_sixth"]

        # 優先判定特別獎
        for sp in special_prizes:
            if sp.matching_number == inv_num:
                matched_amount = sp.amount
                break

        # 次判定特獎
        if matched_amount is None:
            for gp in grand_prizes:
                if gp.matching_number == inv_num:
                    matched_amount = gp.amount
                    break

        # 次判定頭獎與二~六獎
        if matched_amount is None and first_prizes:
            for fp in first_prizes:
                fn = fp.matching_number
                if inv_num == fn:
                    matched_amount = fp.amount
                    break
                elif inv_num[-7:] == fn[-7:] and second_prizes:
                    matched_amount = second_prizes[0].amount
                    break
                elif inv_num[-6:] == fn[-6:] and third_prizes:
                    matched_amount = third_prizes[0].amount
                    break
                elif inv_num[-5:] == fn[-5:] and fourth_prizes:
                    matched_amount = fourth_prizes[0].amount
                    break
                elif inv_num[-4:] == fn[-4:] and fifth_prizes:
                    matched_amount = fifth_prizes[0].amount
                    break
                elif inv_num[-3:] == fn[-3:] and sixth_prizes:
                    matched_amount = sixth_prizes[0].amount
                    break

        # 次判定增開六獎
        if matched_amount is None and extra_sixth_prizes:
            for esp in extra_sixth_prizes:
                m_len = len(esp.matching_number)
                if inv_num[-m_len:] == esp.matching_number:
                    matched_amount = esp.amount
                    break

        if matched_amount is None:
            return json_response(400, "無法比對該發票之獎金金額，請確認開獎期別獎項設定", None)

        # 建立入帳記錄（匯率沿用發票，source 為發票中獎，invoice_id 指向此發票）
        income = Income(
            received_on=date.today(),
            amount=matched_amount,
            currency=invoice.currency,
            fx_rate_to_twd=invoice.fx_rate_to_twd,
            category_id=category_id,
            account_id=account_id,
            source=f"統一發票中獎：{invoice.number}",
            note=None,
            invoice_id=invoice.id,
        )

        invoice.prize_status = "claimed"
        invoice.updated_at = datetime.now(timezone.utc)

        db.session.add(income)
        db.session.commit()

        return json_response(
            201,
            "發票中獎入帳成功",
            {
                "invoice": invoice.to_dict(),
                "income": income.to_dict(),
            },
        )
    except IntegrityError:
        db.session.rollback()
        return json_response(
            400, "發票中獎入帳失敗，資料違反約束條件（例如已重複入帳）", None
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


# ==========================================
# 投資標的、交易與持有 (Instruments & Trades)
# ==========================================


def calculate_instrument_holding(instrument_id: int):
    """依 traded_on ASC, id ASC 重播交易，回傳 (當前持有數量, 平均買進成本)。
    忽略 voided_at 有值的交易。
    平均成本只納入 buy 與 drip 的價格和數量。
    賣出後成本依平均成本等比例減少。
    stock_dividend 增加股數、不增加成本。
    split 與 merge 用 ratio 調整股數，總成本不變。
    capital_reduction：若為純減資/彌補虧損或退資，依 ratio 或股數調整；若無 ratio 且帶 quantity 則依此減少股數。
    """
    trades = (
        Trade.query.filter(
            Trade.instrument_id == instrument_id, Trade.voided_at.is_(None)
        )
        .order_by(Trade.traded_on.asc(), Trade.id.asc())
        .all()
    )

    holding_qty = Decimal("0")
    total_cost = Decimal("0")

    for tr in trades:
        t_type = tr.trade_type
        qty = tr.quantity if tr.quantity is not None else Decimal("0")
        prc = tr.price if tr.price is not None else Decimal("0")
        ratio = tr.ratio if tr.ratio is not None else Decimal("1")

        if t_type in {"buy", "drip"}:
            holding_qty += qty
            total_cost += qty * prc
        elif t_type == "stock_dividend":
            holding_qty += qty
            # 總成本不變
        elif t_type == "sell":
            if holding_qty > Decimal("0"):
                avg_cost = total_cost / holding_qty
                holding_qty -= qty
                if holding_qty <= Decimal("0"):
                    holding_qty = Decimal("0")
                    total_cost = Decimal("0")
                else:
                    total_cost = holding_qty * avg_cost
            else:
                holding_qty -= qty
        elif t_type in {"split", "merge"}:
            if ratio > Decimal("0"):
                holding_qty = holding_qty * ratio
                # 總成本不變
        elif t_type == "capital_reduction":
            if tr.ratio is not None and tr.ratio > Decimal("0"):
                holding_qty = holding_qty * tr.ratio
            elif tr.quantity is not None:
                holding_qty -= tr.quantity
                if holding_qty <= Decimal("0"):
                    holding_qty = Decimal("0")
                    total_cost = Decimal("0")

    avg_buy_cost = (
        (total_cost / holding_qty) if holding_qty > Decimal("0") else Decimal("0")
    )
    return holding_qty, avg_buy_cost


@api_bp.post("/instruments")
def create_instrument():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：market
    raw_market = payload.get("market")
    allowed_markets = {"tw_stock", "us_stock", "fund", "other"}
    if not raw_market or str(raw_market).strip() not in allowed_markets:
        return json_response(
            400, "市場別不合法，只允許 tw_stock、us_stock、fund、other", None
        )
    market = str(raw_market).strip()

    # 必填：symbol
    raw_symbol = payload.get("symbol")
    if not raw_symbol or not isinstance(raw_symbol, str) or not raw_symbol.strip():
        return json_response(400, "缺少必填欄位或欄位為空：symbol（標的代號）", None)
    symbol = raw_symbol.strip()

    # 必填：name
    raw_name = payload.get("name")
    if not raw_name or not isinstance(raw_name, str) or not raw_name.strip():
        return json_response(400, "缺少必填欄位或欄位為空：name（標的名稱）", None)
    name = raw_name.strip()

    # 必填：currency（3 碼）
    raw_currency = payload.get("currency")
    if (
        not raw_currency
        or not isinstance(raw_currency, str)
        or len(raw_currency.strip()) != 3
    ):
        return json_response(400, "幣別代碼必須為 3 碼英文字母", None)
    currency = raw_currency.strip().upper()

    try:
        existing = Instrument.query.filter_by(market=market, symbol=symbol).first()
        if existing:
            return json_response(400, "同一個市場中的標的代號已存在，不可重複建立", None)

        instrument = Instrument(
            market=market,
            symbol=symbol,
            name=name,
            currency=currency,
        )
        db.session.add(instrument)
        db.session.commit()
        return json_response(201, "投資標的建立成功", instrument.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "投資標的資料違反約束條件（例如代號重複）", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/instruments")
def get_instruments():
    try:
        instruments = Instrument.query.order_by(Instrument.id.asc()).all()
        return json_response(
            200, "成功取得投資標的清單", [inst.to_dict() for inst in instruments]
        )
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/instruments/<int:instrument_id>")
def get_instrument(instrument_id):
    try:
        instrument = db.session.get(Instrument, instrument_id)
        if not instrument:
            return json_response(404, "找不到指定的投資標的", None)
        return json_response(200, "成功取得投資標的資料", instrument.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/instruments/<int:instrument_id>/holding")
def get_instrument_holding(instrument_id):
    try:
        instrument = db.session.get(Instrument, instrument_id)
        if not instrument:
            return json_response(404, "找不到指定的投資標的", None)

        qty, avg_cost = calculate_instrument_holding(instrument.id)

        data = {
            "instrument_id": instrument.id,
            "market": instrument.market,
            "symbol": instrument.symbol,
            "name": instrument.name,
            "quantity": float(qty),
            "average_buy_cost": float(avg_cost),
            "currency": instrument.currency,
        }
        return json_response(200, "成功取得投資標的持倉資料", data)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/trades")
def create_trade():
    if not request.is_json:
        return json_response(400, "請使用 JSON 格式傳送請求", None)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_response(400, "請求內容必須為 JSON 物件", None)

    # 必填：instrument_id
    raw_inst_id = payload.get("instrument_id")
    if raw_inst_id is None:
        return json_response(400, "缺少必填欄位：instrument_id（投資標的）", None)
    try:
        instrument_id = int(raw_inst_id)
        inst = db.session.get(Instrument, instrument_id)
        if not inst:
            return json_response(400, "指派的投資標的不存在", None)
    except (ValueError, TypeError):
        return json_response(400, "投資標的 ID 必須為整數", None)

    # 必填：account_id
    raw_acc_id = payload.get("account_id")
    if raw_acc_id is None:
        return json_response(400, "缺少必填欄位：account_id（交割帳戶）", None)
    try:
        account_id = int(raw_acc_id)
        acc = db.session.get(Account, account_id)
        if not acc:
            return json_response(400, "指派的交割帳戶不存在", None)
    except (ValueError, TypeError):
        return json_response(400, "交割帳戶 ID 必須為整數", None)

    # 必填：trade_type
    raw_type = payload.get("trade_type")
    allowed_types = {
        "buy",
        "sell",
        "cash_dividend",
        "stock_dividend",
        "drip",
        "split",
        "merge",
        "capital_reduction",
    }
    if not raw_type or str(raw_type).strip() not in allowed_types:
        return json_response(
            400,
            "交易種類不合法，只允許 buy、sell、cash_dividend、stock_dividend、drip、split、merge、capital_reduction",
            None,
        )
    trade_type = str(raw_type).strip()

    # 必填：traded_on
    raw_traded_on = payload.get("traded_on")
    if not raw_traded_on:
        return json_response(400, "缺少必填欄位：traded_on（交易日期）", None)
    try:
        traded_on = date.fromisoformat(str(raw_traded_on).strip())
    except (ValueError, TypeError):
        return json_response(400, "交易日期格式錯誤，請使用 YYYY-MM-DD 格式", None)

    # 必填：currency
    raw_currency = payload.get("currency")
    if (
        not raw_currency
        or not isinstance(raw_currency, str)
        or len(raw_currency.strip()) != 3
    ):
        return json_response(400, "幣別代碼必須為 3 碼英文字母", None)
    currency = raw_currency.strip().upper()

    # 必填：fx_rate_to_twd
    raw_fx = payload.get("fx_rate_to_twd")
    if raw_fx is None:
        return json_response(400, "缺少必填欄位：fx_rate_to_twd（台幣匯率）", None)
    try:
        fx_rate_to_twd = Decimal(str(raw_fx).strip())
        if fx_rate_to_twd <= Decimal("0"):
            return json_response(400, "台幣匯率必須大於 0", None)
    except (InvalidOperation, ValueError, TypeError):
        return json_response(400, "台幣匯率格式不正確，必須為數值", None)

    # 解析數值欄位
    def parse_optional_decimal(key, field_label, non_negative=False):
        raw = payload.get(key)
        if raw is None or not str(raw).strip():
            return None
        try:
            val = Decimal(str(raw).strip())
            if non_negative and val < Decimal("0"):
                raise ValueError(f"{field_label}不可為負數")
            return val
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f"{field_label}格式不正確，必須為數值")

    try:
        quantity = parse_optional_decimal("quantity", "數量", non_negative=True)
        price = parse_optional_decimal("price", "單價", non_negative=True)
        cash_amount = parse_optional_decimal("cash_amount", "現金金額", non_negative=True)
        ratio = parse_optional_decimal("ratio", "調整比例", non_negative=True)
        fee = parse_optional_decimal("fee", "手續費", non_negative=True)
        tax = parse_optional_decimal("tax", "交易稅", non_negative=True)
    except ValueError as ve:
        return json_response(400, str(ve), None)

    # 依種類要求欄位
    if trade_type in {"buy", "sell", "drip"}:
        if quantity is None or quantity <= Decimal("0"):
            return json_response(400, f"{trade_type} 交易必填數量（quantity）且必須大於 0", None)
        if price is None or price < Decimal("0"):
            return json_response(400, f"{trade_type} 交易必填價格（price）", None)
    elif trade_type == "stock_dividend":
        if quantity is None or quantity <= Decimal("0"):
            return json_response(400, "股票股利必填數量（quantity）且必須大於 0", None)
    elif trade_type in {"split", "merge"}:
        if ratio is None or ratio <= Decimal("0"):
            return json_response(400, f"{trade_type} 交易必填比例（ratio）且必須大於 0", None)
    elif trade_type == "cash_dividend":
        if cash_amount is None or cash_amount <= Decimal("0"):
            return json_response(400, "現金股利必填現金金額（cash_amount）且必須大於 0", None)
    elif trade_type == "capital_reduction":
        if (cash_amount is None or cash_amount <= Decimal("0")) and (
            ratio is None and quantity is None
        ):
            return json_response(
                400, "減資交易需填寫退回現金金額（cash_amount）或減資比例／股數", None
            )

    # 關鍵防護：sell 與 capital_reduction 若會使持有數量小於 0，回傳 400，不要寫入
    current_holding, _ = calculate_instrument_holding(inst.id)
    if trade_type == "sell":
        if quantity is not None and quantity > current_holding:
            return json_response(
                400,
                f"賣出數量（{quantity}）超過目前持有股數（{current_holding}），拒絕寫入",
                None,
            )
    elif trade_type == "capital_reduction":
        if quantity is not None and quantity > current_holding:
            return json_response(
                400,
                f"減資銷除股數（{quantity}）超過目前持有股數（{current_holding}），拒絕寫入",
                None,
            )

    # 檢查是否需要同步入帳 post_income
    post_income = payload.get("post_income")
    should_post_income = False
    income_category_id = None

    if post_income is True and trade_type in {"cash_dividend", "capital_reduction"} and cash_amount is not None and cash_amount > Decimal("0"):
        raw_inc_cat = payload.get("income_category_id")
        if raw_inc_cat is None:
            return json_response(400, "請求同步入帳時，必填 income_category_id（入帳分類）", None)
        try:
            income_category_id = int(raw_inc_cat)
            cat = db.session.get(IncomeCategory, income_category_id)
            if not cat:
                return json_response(400, "指派的入帳分類不存在", None)
        except (ValueError, TypeError):
            return json_response(400, "入帳分類 ID 必須為整數", None)
        should_post_income = True

    try:
        trade = Trade(
            instrument_id=instrument_id,
            account_id=account_id,
            trade_type=trade_type,
            traded_on=traded_on,
            quantity=quantity,
            price=price,
            cash_amount=cash_amount,
            ratio=ratio,
            fee=fee,
            tax=tax,
            currency=currency,
            fx_rate_to_twd=fx_rate_to_twd,
        )
        db.session.add(trade)
        db.session.flush()  # 取得 trade.id

        if should_post_income:
            income = Income(
                received_on=traded_on,
                amount=cash_amount,
                currency=currency,
                fx_rate_to_twd=fx_rate_to_twd,
                category_id=income_category_id,
                account_id=account_id,
                source=f"投資收益：{inst.symbol} {inst.name} ({trade_type})",
                note=None,
                trade_id=trade.id,
            )
            db.session.add(income)

        db.session.commit()
        return json_response(201, "投資交易新增成功", trade.to_dict())
    except IntegrityError:
        db.session.rollback()
        return json_response(400, "交易資料違反約束條件", None)
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.get("/trades")
def get_trades():
    try:
        query = Trade.query

        raw_inst_id = request.args.get("instrument_id")
        if raw_inst_id is not None:
            try:
                inst_id = int(raw_inst_id)
                query = query.filter(Trade.instrument_id == inst_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 instrument_id 必須為整數", None)

        raw_acc_id = request.args.get("account_id")
        if raw_acc_id is not None:
            try:
                acc_id = int(raw_acc_id)
                query = query.filter(Trade.account_id == acc_id)
            except (ValueError, TypeError):
                return json_response(400, "查詢參數 account_id 必須為整數", None)

        trades = query.order_by(Trade.traded_on.asc(), Trade.id.asc()).all()
        return json_response(200, "成功取得投資交易清單", [tr.to_dict() for tr in trades])
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)


@api_bp.post("/trades/<int:trade_id>/void")
def void_trade(trade_id):
    try:
        trade = db.session.get(Trade, trade_id)
        if not trade:
            return json_response(404, "找不到指定的投資交易", None)

        if trade.voided_at is not None:
            return json_response(400, "此交易先前已作廢，不可重複作廢", None)

        # 若這筆交易已有入帳，回 400，不要作廢
        existing_income = Income.query.filter_by(trade_id=trade.id).first()
        if existing_income:
            return json_response(
                400, "此交易已關聯入帳記錄，請先處理關聯入帳後再作廢", None
            )

        trade.voided_at = datetime.now(timezone.utc)
        trade.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return json_response(200, "投資交易作廢成功", trade.to_dict())
    except Exception:
        db.session.rollback()
        return json_response(500, "資料庫暫時連不上", None)



