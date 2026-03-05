from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json
from urllib import request, error, parse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(ENV_PATH)

app = FastAPI(title="Flint Wifi Management API")

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/config")
def config():
    return {
        "cors_origins": origins,
        "supabase_url": os.getenv("SUPABASE_URL", ""),
    }

def _supabase_headers() -> Dict[str, str]:
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
    if not supabase_anon_key:
        raise HTTPException(status_code=500, detail="Supabase is not configured.")
    return {
        "apikey": supabase_anon_key,
        "Authorization": f"Bearer {supabase_anon_key}",
        "Content-Type": "application/json",
    }


def _supabase_rest_base() -> str:
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        raise HTTPException(status_code=500, detail="Supabase is not configured.")
    return f"{supabase_url}/rest/v1"


def _supabase_get(
    path: str,
    query: str = "",
    params: Optional[Union[Dict[str, str], List[Tuple[str, str]]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    base = _supabase_rest_base()
    url = f"{base}/{path}"
    if params:
        if isinstance(params, dict):
            params = list(params.items())
        query = parse.urlencode(params, safe=":,.")
    if query:
        url = f"{url}?{query}"
    headers = _supabase_headers()
    if extra_headers:
        headers.update(extra_headers)
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body else []
            return data, dict(resp.headers)
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"message": "Supabase request failed."}
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail="Supabase unavailable.") from exc


def _supabase_request(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    query: str = "",
    params: Optional[Union[Dict[str, str], List[Tuple[str, str]]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    base = _supabase_rest_base()
    url = f"{base}/{path}"
    if params:
        if isinstance(params, dict):
            params = list(params.items())
        query = parse.urlencode(params, safe=":,.")
    if query:
        url = f"{url}?{query}"
    headers = _supabase_headers()
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=20) as resp:
            body_text = resp.read().decode("utf-8")
            data = json.loads(body_text) if body_text else None
            return data
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"message": "Supabase request failed."}
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail="Supabase unavailable.") from exc


def _parse_content_range_total(headers: Dict[str, str]) -> int:
    content_range = headers.get("Content-Range") or headers.get("content-range")
    if not content_range or "/" not in content_range:
        return 0
    total = content_range.split("/")[-1]
    if total == "*":
        return 0
    try:
        return int(total)
    except ValueError:
        return 0


def _count_rows(
    table: str, filters: Optional[List[Tuple[str, str]]] = None
) -> int:
    params: List[Tuple[str, str]] = [("select", "id")]
    if filters:
        params.extend(filters)
    _, headers = _supabase_get(
        table,
        params=params,
        extra_headers={"Prefer": "count=exact"},
    )
    return _parse_content_range_total(headers)


def _sum_amounts(rows: List[Dict[str, Any]], field: str = "amount") -> float:
    total = 0.0
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        try:
            total += float(value)
        except (TypeError, ValueError):
            continue
    return total


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@app.get("/dashboard/summary")
def dashboard_summary():
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)
    today_start_iso = today_start.isoformat()
    tomorrow_start_iso = tomorrow_start.isoformat()
    active_window_start = now - timedelta(days=30)

    revenue_rows, _ = _supabase_get(
        "Transactions",
        params=[
            ("select", "amount"),
            ("created_at", f"gte.{today_start_iso}"),
            ("created_at", f"lt.{tomorrow_start_iso}"),
        ],
    )
    revenue_today = _sum_amounts(revenue_rows, "amount")

    credentials_sold_today = _count_rows(
        "SoldLogins",
        filters=[
            ("created_at", f"gte.{today_start_iso}"),
            ("created_at", f"lt.{tomorrow_start_iso}"),
        ],
    )

    remaining_credentials = _count_rows("Logins")

    sold_rows, _ = _supabase_get(
        "SoldLogins",
        params=[
            ("select", "created_at,plan_type"),
            ("created_at", f"gte.{active_window_start.isoformat()}"),
            ("plan_type", "neq.1.5-Gigabyte"),
            ("order", "created_at.desc"),
            ("limit", "10000"),
        ],
    )

    active_users = 0
    plan_days = {"daily": 1, "weekly": 7, "monthly": 30}
    for row in sold_rows:
        created_at = _parse_iso_datetime(row.get("created_at", ""))
        if not created_at:
            continue
        plan_type = (row.get("plan_type") or "").strip().lower()
        if plan_type not in plan_days:
            continue
        expires_at = created_at.astimezone(timezone.utc) + timedelta(
            days=plan_days[plan_type]
        )
        if now <= expires_at:
            active_users += 1

    return {
        "revenue_today": revenue_today,
        "credentials_sold_today": credentials_sold_today,
        "active_users": active_users,
        "remaining_credentials": remaining_credentials,
        "currency": "GH₵",
        "as_of": now.isoformat(),
    }


@app.get("/dashboard/sales")
def dashboard_sales():
    now = datetime.now(timezone.utc)
    window_days = 7
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    window_start = today_start - timedelta(days=window_days - 1)
    window_end = today_start + timedelta(days=1)

    rows, _ = _supabase_get(
        "Transactions",
        params=[
            ("select", "amount,created_at"),
            ("created_at", f"gte.{window_start.isoformat()}"),
            ("created_at", f"lt.{window_end.isoformat()}"),
        ],
    )

    buckets: Dict[datetime, float] = {}
    for i in range(window_days):
        bucket_time = window_start + timedelta(days=i)
        buckets[bucket_time] = 0.0

    for row in rows:
        created_at = _parse_iso_datetime(row.get("created_at", ""))
        if not created_at:
            continue
        created_at = created_at.astimezone(timezone.utc)
        bucket_time = created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket_time in buckets:
            try:
                buckets[bucket_time] += float(row.get("amount") or 0)
            except (TypeError, ValueError):
                continue

    points = []
    for bucket_time in sorted(buckets.keys()):
        label = f"{bucket_time.strftime('%b')} {bucket_time.day}"
        points.append({"label": label, "amount": round(buckets[bucket_time], 2)})

    return {
        "window_days": window_days,
        "currency": "GH₵",
        "points": points,
        "as_of": now.isoformat(),
    }


@app.get("/credentials/summary")
def credentials_summary():
    plans, _ = _supabase_get(
        "Plans",
        params=[
            ("select", "plan_type,amount,description,enabled"),
            ("order", "plan_type.asc"),
        ],
    )
    login_rows, _ = _supabase_get(
        "Logins",
        params=[("select", "type")],
    )

    counts: Dict[str, int] = {}
    for row in login_rows:
        plan_type = (row.get("type") or "").strip()
        if not plan_type:
            continue
        counts[plan_type] = counts.get(plan_type, 0) + 1

    summary: List[Dict[str, Any]] = []
    seen = set()
    for plan in plans:
        plan_type = (plan.get("plan_type") or "").strip()
        if not plan_type:
            continue
        summary.append(
            {
                "plan_type": plan_type,
                "remaining": counts.get(plan_type, 0),
                "amount": plan.get("amount"),
                "description": plan.get("description"),
                "enabled": bool(plan.get("enabled", True)),
            }
        )
        seen.add(plan_type)

    for plan_type, remaining in counts.items():
        if plan_type in seen:
            continue
        summary.append(
            {
                "plan_type": plan_type,
                "remaining": remaining,
                "amount": None,
                "description": None,
                "enabled": True,
            }
        )

    return {"plans": summary, "as_of": datetime.now(timezone.utc).isoformat()}


def _parse_date_range(
    date_from: Optional[str], date_to: Optional[str]
) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []
    if date_from:
        try:
            start = datetime.fromisoformat(date_from).date()
            start_dt = datetime(
                start.year, start.month, start.day, tzinfo=timezone.utc
            )
            filters.append(("created_at", f"gte.{start_dt.isoformat()}"))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to).date() + timedelta(days=1)
            end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
            filters.append(("created_at", f"lt.{end_dt.isoformat()}"))
        except ValueError:
            pass
    return filters


def _resolve_date_window(
    date_from: Optional[str], date_to: Optional[str], window_days: int = 7
) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _to_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value).date()
            return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        except ValueError:
            return None

    start = _to_date(date_from)
    end = _to_date(date_to)

    if not start and not end:
        start = today - timedelta(days=window_days - 1)
        end = today
    elif start and not end:
        end = start + timedelta(days=max(window_days - 1, 0))
    elif end and not start:
        start = end - timedelta(days=max(window_days - 1, 0))

    if not start or not end:
        start = today - timedelta(days=window_days - 1)
        end = today
    elif end < start:
        start, end = end, start

    end_exclusive = end + timedelta(days=1)
    return start, end_exclusive


def _start_of_day(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _start_of_week(value: datetime) -> datetime:
    # ISO week starts on Monday.
    weekday = value.weekday()  # Monday=0
    return _start_of_day(value) - timedelta(days=weekday)


def _start_of_month(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=timezone.utc)


def _add_months(value: datetime, months: int) -> datetime:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _resolve_bucket_window(
    date_from: Optional[str],
    date_to: Optional[str],
    bucket: str,
    window_units: int,
    *,
    full_range: bool = False,
    plan_type: Optional[str] = None,
) -> Tuple[datetime, datetime]:
    window_units = max(window_units, 1)
    now = datetime.now(timezone.utc)
    today = _start_of_day(now)

    if full_range:
        earliest_params: List[Tuple[str, str]] = [
            ("select", "created_at"),
            ("order", "created_at.asc"),
            ("limit", "1"),
        ]
        if plan_type:
            earliest_params.append(("plan_type", f"eq.{plan_type}"))
        earliest_rows, _ = _supabase_get("Transactions", params=earliest_params)
        earliest = None
        if earliest_rows:
            earliest = _parse_iso_datetime(earliest_rows[0].get("created_at", ""))
        if earliest:
            start = _start_of_day(earliest.astimezone(timezone.utc))
        else:
            start = today
        end_exclusive = today + timedelta(days=1)
        return start, end_exclusive

    if date_from or date_to:
        start, end_exclusive = _resolve_date_window(date_from, date_to, window_units)
        return _start_of_day(start), _start_of_day(end_exclusive)

    if bucket == "weekly":
        week_start = _start_of_week(today)
        end_exclusive = week_start + timedelta(days=7)
        start = end_exclusive - timedelta(days=window_units * 7)
        return start, end_exclusive

    if bucket == "monthly":
        month_start = _start_of_month(today)
        end_exclusive = _add_months(month_start, 1)
        start = _add_months(end_exclusive, -window_units)
        return start, end_exclusive

    end_exclusive = today + timedelta(days=1)
    start = end_exclusive - timedelta(days=window_units)
    return start, end_exclusive


def _iterate_transactions(
    filters: Optional[List[Tuple[str, str]]] = None,
    select: str = "customer_email,credential_username,amount,plan_type,created_at",
) -> List[Dict[str, Any]]:
    params: List[Tuple[str, str]] = [("select", select)]
    if filters:
        params.extend(filters)

    offset = 0
    page_size = 1000
    all_rows: List[Dict[str, Any]] = []
    while True:
        paged_params = params + [("limit", str(page_size)), ("offset", str(offset))]
        rows, _ = _supabase_get("Transactions", params=paged_params)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def _aggregate_customers(
    filters: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Dict[str, Any]]:
    customers: Dict[str, Dict[str, Any]] = {}
    rows = _iterate_transactions(filters)
    for row in rows:
        email = (row.get("customer_email") or "").strip()
        username = (row.get("credential_username") or "").strip()
        key = (email or username or "unknown").lower()
        entry = customers.setdefault(
            key,
            {
                "key": key,
                "display_name": email or username or "Unknown",
                "email": email or None,
                "username": username or None,
                "purchases": 0,
                "total_spent": 0.0,
                "last_purchase_at": None,
            },
        )
        entry["purchases"] += 1
        try:
            entry["total_spent"] += float(row.get("amount") or 0)
        except (TypeError, ValueError):
            pass
        created_at = _parse_iso_datetime(row.get("created_at", ""))
        if created_at:
            if not entry["last_purchase_at"] or created_at > entry["last_purchase_at"]:
                entry["last_purchase_at"] = created_at
    return customers


@app.get("/credentials/logins")
def credentials_logins(
    search: Optional[str] = None,
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    params: List[Tuple[str, str]] = [
        ("select", "username,password,type,created_at"),
        ("order", "username.asc"),
        ("limit", str(page_size)),
        ("offset", str(offset)),
    ]

    if plan_type:
        params.append(("type", f"eq.{plan_type}"))

    if search:
        term = search.strip()
        if term:
            params.append(("username", f"ilike.*{term}*"))

    params.extend(_parse_date_range(date_from, date_to))

    rows, headers = _supabase_get(
        "Logins",
        params=params,
        extra_headers={"Prefer": "count=exact"},
    )
    total = _parse_content_range_total(headers)
    return {
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/credentials/sold")
def credentials_sold(
    search: Optional[str] = None,
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    params: List[Tuple[str, str]] = [
        ("select", "username,password,customer_email,created_at,plan_type"),
        ("order", "created_at.desc"),
        ("limit", str(page_size)),
        ("offset", str(offset)),
    ]

    if plan_type:
        params.append(("plan_type", f"eq.{plan_type}"))

    if search:
        term = search.strip()
        if term:
            params.append(
                (
                    "or",
                    f"(username.ilike.*{term}*,customer_email.ilike.*{term}*)",
                )
            )

    params.extend(_parse_date_range(date_from, date_to))

    rows, headers = _supabase_get(
        "SoldLogins",
        params=params,
        extra_headers={"Prefer": "count=exact"},
    )
    total = _parse_content_range_total(headers)
    return {
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/sales/summary")
def sales_summary(
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    params: List[Tuple[str, str]] = [
        ("select", "amount,plan_type"),
    ]

    if plan_type:
        params.append(("plan_type", f"eq.{plan_type}"))

    date_filters = _parse_date_range(date_from, date_to)
    params.extend(date_filters)

    sold_filters: List[Tuple[str, str]] = []
    if plan_type:
        sold_filters.append(("plan_type", f"eq.{plan_type}"))
    sold_filters.extend(date_filters)
    logins_sold = _count_rows("SoldLogins", filters=sold_filters)

    total_revenue = 0.0
    transaction_count = 0
    plan_totals: Dict[str, Dict[str, Union[int, float]]] = {}
    plan_options = set()

    offset = 0
    page_size = 1000
    while True:
        paged_params = params + [("limit", str(page_size)), ("offset", str(offset))]
        rows, _ = _supabase_get("Transactions", params=paged_params)
        if not rows:
            break

        transaction_count += len(rows)
        total_revenue += _sum_amounts(rows, "amount")

        for row in rows:
            plan = (row.get("plan_type") or "").strip() or "Unknown"
            plan_options.add(plan)
            entry = plan_totals.setdefault(plan, {"count": 0, "revenue": 0.0})
            entry["count"] = int(entry["count"]) + 1
            try:
                entry["revenue"] = float(entry["revenue"]) + float(
                    row.get("amount") or 0
                )
            except (TypeError, ValueError):
                continue

        if len(rows) < page_size:
            break
        offset += page_size

    avg_sales_per_day = None
    if transaction_count:
        if date_from or date_to:
            start, end_exclusive = _resolve_date_window(date_from, date_to, 1)
            days = max((end_exclusive - start).days, 1)
            avg_sales_per_day = total_revenue / days if days else None
        else:
            earliest_params: List[Tuple[str, str]] = [
                ("select", "created_at"),
                ("order", "created_at.asc"),
                ("limit", "1"),
            ]
            if plan_type:
                earliest_params.append(("plan_type", f"eq.{plan_type}"))
            earliest_rows, _ = _supabase_get("Transactions", params=earliest_params)
            earliest = None
            if earliest_rows:
                earliest = _parse_iso_datetime(earliest_rows[0].get("created_at", ""))
            if earliest:
                start = _start_of_day(earliest.astimezone(timezone.utc))
                today = datetime.now(timezone.utc)
                end_exclusive = _start_of_day(today) + timedelta(days=1)
                days = max((end_exclusive - start).days, 1)
                avg_sales_per_day = total_revenue / days if days else None

    top_plan = None
    top_sold_plan = None
    if plan_totals:
        top_plan = max(
            plan_totals.items(),
            key=lambda item: (item[1]["revenue"], item[1]["count"]),
        )
        top_plan = {
            "plan_type": top_plan[0],
            "count": int(top_plan[1]["count"]),
            "revenue": round(float(top_plan[1]["revenue"]), 2),
        }
        top_sold = max(
            plan_totals.items(),
            key=lambda item: (item[1]["count"], item[1]["revenue"]),
        )
        top_sold_plan = {
            "plan_type": top_sold[0],
            "count": int(top_sold[1]["count"]),
            "revenue": round(float(top_sold[1]["revenue"]), 2),
        }

    plan_options = sorted({option for option in plan_options if option})
    plan_breakdown = [
        {
            "plan_type": plan,
            "count": int(plan_totals[plan]["count"]),
            "revenue": round(float(plan_totals[plan]["revenue"]), 2),
        }
        for plan in sorted(plan_totals.keys())
    ]

    return {
        "total_revenue": round(total_revenue, 2),
        "transactions": transaction_count,
        "logins_sold": logins_sold,
        "avg_sales_per_day": round(avg_sales_per_day, 2)
        if avg_sales_per_day is not None
        else None,
        "top_plan": top_plan,
        "top_sold_plan": top_sold_plan,
        "plan_breakdown": plan_breakdown,
        "plan_options": plan_options,
        "currency": "GH₵",
        "range": {"from": date_from, "to": date_to},
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/sales/transactions")
def sales_transactions(
    search: Optional[str] = None,
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    params: List[Tuple[str, str]] = [
        (
            "select",
            "payment_reference,customer_email,plan_type,amount,credential_username,created_at",
        ),
        ("order", "created_at.desc"),
        ("limit", str(page_size)),
        ("offset", str(offset)),
    ]

    if plan_type:
        params.append(("plan_type", f"eq.{plan_type}"))

    if search:
        term = search.strip()
        if term:
            params.append(
                (
                    "or",
                    (
                        f"(payment_reference.ilike.*{term}*,"
                        f"customer_email.ilike.*{term}*,"
                        f"credential_username.ilike.*{term}*)"
                    ),
                )
            )

    params.extend(_parse_date_range(date_from, date_to))

    rows, headers = _supabase_get(
        "Transactions",
        params=params,
        extra_headers={"Prefer": "count=exact"},
    )
    total = _parse_content_range_total(headers)
    return {
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/sales/history")
def sales_history(
    search: Optional[str] = None,
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    params: List[Tuple[str, str]] = [
        (
            "select",
            "username,customer_email,plan_type,payment_reference,created_at",
        ),
        ("order", "created_at.desc"),
        ("limit", str(page_size)),
        ("offset", str(offset)),
    ]

    if plan_type:
        params.append(("plan_type", f"eq.{plan_type}"))

    if search:
        term = search.strip()
        if term:
            params.append(
                (
                    "or",
                    (
                        f"(username.ilike.*{term}*,"
                        f"customer_email.ilike.*{term}*,"
                        f"payment_reference.ilike.*{term}*)"
                    ),
                )
            )

    params.extend(_parse_date_range(date_from, date_to))

    rows, headers = _supabase_get(
        "SoldLogins",
        params=params,
        extra_headers={"Prefer": "count=exact"},
    )
    total = _parse_content_range_total(headers)
    return {
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/customers/summary")
def customers_summary(
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    filters = _parse_date_range(date_from, date_to)
    customers = _aggregate_customers(filters)

    rows = list(customers.values())
    if search:
        term = search.strip().lower()
        if term:
            rows = [
                row
                for row in rows
                if term in (row.get("display_name") or "").lower()
                or term in (row.get("email") or "").lower()
                or term in (row.get("username") or "").lower()
            ]

    rows.sort(
        key=lambda row: (row.get("total_spent", 0), row.get("purchases", 0)),
        reverse=True,
    )

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    paged = rows[start:end]

    for row in paged:
        last_purchase_at = row.get("last_purchase_at")
        row["last_purchase_at"] = (
            last_purchase_at.isoformat() if last_purchase_at else None
        )
        row["total_spent"] = round(float(row.get("total_spent") or 0), 2)
        row["currency"] = "GH₵"

    return {
        "rows": paged,
        "page": page,
        "page_size": page_size,
        "total": total,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/customers/gifts")
def customers_gifts(
    limit: int = 6,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    limit = max(min(limit, 50), 1)
    params: List[Tuple[str, str]] = [
        ("select", "username,customer_email,plan_type,created_at"),
        ("order", "created_at.desc"),
        ("limit", str(limit)),
        ("or", "(customer_email.is.null,customer_email.eq.)"),
    ]
    params.extend(_parse_date_range(date_from, date_to))
    rows, _ = _supabase_get("SoldLogins", params=params)
    return {"rows": rows, "as_of": datetime.now(timezone.utc).isoformat()}


@app.get("/sales/by-date")
def sales_by_date(
    plan_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    window_days: int = 7,
    bucket: str = "daily",
    window_units: int = 7,
    bucket_size: int = 1,
    full_range: bool = False,
):
    bucket = (bucket or "daily").lower()
    if bucket not in {"hourly", "daily", "weekly", "monthly"}:
        bucket = "daily"

    bucket_size = max(int(bucket_size or 1), 1)

    if date_from or date_to or full_range:
        start, end_exclusive = _resolve_bucket_window(
            date_from,
            date_to,
            bucket,
            window_units,
            full_range=full_range,
            plan_type=plan_type,
        )
    else:
        if bucket == "daily":
            window_units = max(window_days, 1)
        start, end_exclusive = _resolve_bucket_window(
            None, None, bucket, window_units
        )

    params: List[Tuple[str, str]] = [
        ("select", "amount,created_at"),
        ("created_at", f"gte.{start.isoformat()}"),
        ("created_at", f"lt.{end_exclusive.isoformat()}"),
    ]

    if plan_type:
        params.append(("plan_type", f"eq.{plan_type}"))

    rows, _ = _supabase_get("Transactions", params=params)

    now = datetime.now(timezone.utc)
    effective_end = min(end_exclusive, now)
    if effective_end < start:
        effective_end = start

    buckets: Dict[datetime, float] = {}
    cursor = start
    while cursor < effective_end:
        buckets[cursor] = 0.0
        if bucket == "monthly":
            cursor = _add_months(cursor, bucket_size)
        elif bucket == "weekly":
            cursor += timedelta(days=7 * bucket_size)
        elif bucket == "daily":
            cursor += timedelta(days=bucket_size)
        else:
            cursor += timedelta(hours=bucket_size)

    def _bucket_key(value: datetime) -> datetime:
        if bucket == "monthly":
            months_diff = (value.year - start.year) * 12 + (value.month - start.month)
            offset = (months_diff // bucket_size) * bucket_size
            return _add_months(start, offset)
        if bucket == "weekly":
            delta_days = (value - start).days
            offset = (delta_days // (7 * bucket_size)) * (7 * bucket_size)
            return start + timedelta(days=offset)
        if bucket == "daily":
            delta_days = (value - start).days
            offset = (delta_days // bucket_size) * bucket_size
            return start + timedelta(days=offset)
        hours = int((value - start).total_seconds() // 3600)
        offset = (hours // bucket_size) * bucket_size
        return start + timedelta(hours=offset)

    def _day_label(value: datetime) -> str:
        return f"{value.strftime('%b')} {value.day}"

    def _time_label(value: datetime) -> str:
        hour = value.hour
        if hour == 0:
            return "12am"
        if hour < 12:
            return f"{hour}am"
        if hour == 12:
            return "12pm"
        return f"{hour - 12}pm"

    for row in rows:
        created_at = _parse_iso_datetime(row.get("created_at", ""))
        if not created_at:
            continue
        created_at = created_at.astimezone(timezone.utc)
        bucket_time = _bucket_key(created_at)
        if bucket_time in buckets:
            try:
                buckets[bucket_time] += float(row.get("amount") or 0)
            except (TypeError, ValueError):
                continue

    points = []
    total_days = max((effective_end - start).days, 0)
    for bucket_time in sorted(buckets.keys()):
        if bucket == "monthly":
            if bucket_size == 1:
                label = bucket_time.strftime("%b %Y")
            else:
                end_bucket = _add_months(bucket_time, bucket_size) - timedelta(days=1)
                label = (
                    f"{bucket_time.strftime('%b %Y')} - "
                    f"{end_bucket.strftime('%b %Y')}"
                )
        elif bucket == "weekly":
            end_label = _day_label(
                bucket_time + timedelta(days=(7 * bucket_size) - 1)
            )
            label = f"{_day_label(bucket_time)} - {end_label}"
        elif bucket == "daily":
            if bucket_size == 1:
                label = _day_label(bucket_time)
            else:
                end_label = _day_label(
                    bucket_time + timedelta(days=bucket_size - 1)
                )
                label = f"{_day_label(bucket_time)} - {end_label}"
        else:
            start_time = _time_label(bucket_time)
            end_time = _time_label(
                (bucket_time + timedelta(hours=bucket_size)) - timedelta(minutes=1)
            )
            time_range = f"{start_time} - {end_time}"
            if total_days > 1:
                label = f"{_day_label(bucket_time)} {time_range}"
            else:
                label = time_range
        points.append(
            {
                "label": label,
                "date": bucket_time.date().isoformat(),
                "amount": round(buckets[bucket_time], 2),
            }
        )

    return {
        "window_days": len(points),
        "currency": "GH₵",
        "points": points,
        "range": {
            "from": start.date().isoformat(),
            "to": (end_exclusive - timedelta(days=1)).date().isoformat(),
        },
        "bucket": bucket,
        "window_units": window_units,
        "bucket_size": bucket_size,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/login")
def login(payload: LoginRequest):
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_anon_key:
        raise HTTPException(status_code=500, detail="Supabase is not configured.")

    login_url = f"{supabase_url}/auth/v1/token?grant_type=password"
    body = json.dumps(
        {"email": payload.email, "password": payload.password}
    ).encode("utf-8")
    headers = {
        "apikey": supabase_anon_key,
        "Authorization": f"Bearer {supabase_anon_key}",
        "Content-Type": "application/json",
    }

    req = request.Request(login_url, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"message": "Login failed."}
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail="Supabase auth unavailable.") from exc


class PlanCreate(BaseModel):
    plan_type: str
    amount: float
    description: Optional[str] = None
    enabled: Optional[bool] = True


class PlanUpdate(BaseModel):
    amount: Optional[float] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class CredentialDeleteRequest(BaseModel):
    username: str
    password: str


@app.get("/plans")
def plans_list():
    rows, _ = _supabase_get(
        "Plans",
        params=[
            ("select", "plan_type,amount,description,enabled,created_at"),
            ("order", "plan_type.asc"),
        ],
    )
    return {"plans": rows, "as_of": datetime.now(timezone.utc).isoformat()}


@app.post("/plans")
def plans_create(payload: PlanCreate):
    plan_type = payload.plan_type.strip()
    if not plan_type:
        raise HTTPException(status_code=400, detail="Plan type is required.")
    body = {
        "plan_type": plan_type,
        "amount": payload.amount,
        "description": payload.description,
        "enabled": bool(payload.enabled) if payload.enabled is not None else True,
    }
    rows = _supabase_request(
        "POST",
        "Plans",
        body=body,
        extra_headers={"Prefer": "return=representation"},
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return body


@app.patch("/plans/{plan_type}")
def plans_update(plan_type: str, payload: PlanUpdate):
    plan_type = plan_type.strip()
    if not plan_type:
        raise HTTPException(status_code=400, detail="Plan type is required.")
    update_body: Dict[str, Any] = {}
    if payload.amount is not None:
        update_body["amount"] = payload.amount
    if payload.description is not None:
        update_body["description"] = payload.description
    if payload.enabled is not None:
        update_body["enabled"] = payload.enabled
    if not update_body:
        raise HTTPException(status_code=400, detail="No fields provided to update.")
    rows = _supabase_request(
        "PATCH",
        "Plans",
        body=update_body,
        params={"plan_type": f"eq.{plan_type}"},
        extra_headers={"Prefer": "return=representation"},
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return update_body


@app.delete("/plans/{plan_type}")
def plans_delete(plan_type: str):
    plan_type = plan_type.strip()
    if not plan_type:
        raise HTTPException(status_code=400, detail="Plan type is required.")
    _supabase_request(
        "DELETE",
        "Plans",
        params={"plan_type": f"eq.{plan_type}"},
    )
    return {"status": "deleted", "plan_type": plan_type}


@app.post("/credentials/delete")
def credentials_delete(payload: CredentialDeleteRequest):
    username = (payload.username or "").strip()
    password = (payload.password or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required.")

    required_password = os.getenv("PANEL_ADMIN_PASSWORD", "")
    if required_password and password != required_password:
        raise HTTPException(status_code=401, detail="Invalid password.")

    _supabase_request(
        "DELETE",
        "Logins",
        params={"username": f"eq.{username}"},
    )
    return {"status": "deleted", "username": username}

