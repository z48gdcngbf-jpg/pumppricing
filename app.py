from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from functools import wraps
from statistics import mean
from time import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from flask import Flask, flash, has_request_context, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
try:
    import akshare as ak
except ImportError:
    ak = None

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pump-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///pump_quote_analysis.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

ALLOWED_DOMAIN = "@danaipumps.cn"

MATERIAL_OPTIONS = ["304", "316", "316L", "双相钢", "哈氏合金", "衬氟", "高铬"]
PROCESS_OPTIONS = ["铸造方式", "CNC加工", "热处理", "动平衡", "焊接工艺", "喷砂喷漆"]
REGION_OPTIONS = ["江苏", "浙江", "山东", "广东"]
BRAND_OPTIONS = ["国产普通", "国产中高端", "国际品牌"]
COMPONENT_OPTIONS = [
    "整泵报价",
    "机械密封报价",
    "电机报价",
    "泵体铸件报价",
    "叶轮报价",
    "轴承报价",
    "法兰与标准件报价",
    "外协加工报价",
    "底座报价",
]

RAW_MATERIALS = [
    {
        "name": "铜",
        "exchange": "上海期货交易所",
        "exchange_code": "CU",
        "target_contract": "cu2607",
        "sina_code": "nf_CU2607",
        "product_code": "cu_f",
        "unit": "元/吨",
        "source_page": "https://www.shfe.cn/products/futures/metal/nonferrousmetal/cu_f/",
        "sina_page": "https://finance.sina.com.cn/futures/quotes/CU2607.shtml",
    },
    {
        "name": "铝",
        "exchange": "上海期货交易所",
        "exchange_code": "AL",
        "target_contract": "al2607",
        "sina_code": "nf_AL2607",
        "product_code": "al_f",
        "unit": "元/吨",
        "source_page": "https://www.shfe.cn/products/futures/metal/nonferrousmetal/al_f/",
        "sina_page": "https://finance.sina.com.cn/futures/quotes/AL2607.shtml",
    },
    {
        "name": "镍",
        "exchange": "上海期货交易所",
        "exchange_code": "NI",
        "target_contract": "ni2607",
        "sina_code": "nf_NI2607",
        "product_code": "ni_f",
        "unit": "元/吨",
        "source_page": "https://www.shfe.cn/products/futures/metal/nonferrousmetal/ni_f/",
        "sina_page": "https://finance.sina.com.cn/futures/quotes/NI2607.shtml",
    },
    {
        "name": "不锈钢",
        "exchange": "上海期货交易所",
        "exchange_code": "SS",
        "target_contract": "ss2607",
        "sina_code": "nf_SS2607",
        "product_code": "ss_f",
        "unit": "元/吨",
        "source_page": "https://www.shfe.cn/products/futures/metal/ferrousandpreciousmetal/ss_f/",
        "sina_page": "https://finance.sina.com.cn/futures/quotes/SS2607.shtml",
    },
    {
        "name": "天然橡胶",
        "exchange": "上海期货交易所",
        "exchange_code": "RU",
        "target_contract": "ru2607",
        "sina_code": "nf_RU2607",
        "product_code": "ru_f",
        "unit": "元/吨",
        "source_page": "https://www.shfe.cn/products/futures/chemical/rubber/ru_f/",
        "sina_page": "https://finance.sina.com.cn/futures/quotes/RU2607.shtml",
    },
]

COMPONENT_DRIVERS = {
    "整泵报价": ["不锈钢", "镍", "铜", "铝", "天然橡胶"],
    "机械密封报价": ["不锈钢", "天然橡胶"],
    "电机报价": ["铜", "铝"],
    "泵体铸件报价": ["不锈钢", "镍"],
    "叶轮报价": ["不锈钢", "镍"],
    "轴承报价": ["不锈钢"],
    "法兰与标准件报价": ["不锈钢"],
    "外协加工报价": ["不锈钢", "镍"],
    "底座报价": ["不锈钢"],
}

# ── Bill of Materials: default material weight (kg) per component ──────────
# Weights represent typical mid-range acid-resistant pump components.
# Users can override every value inline on the dashboard.
COMPONENT_BOM = {
    "整泵报价":       {"不锈钢": 80.0, "镍": 6.0, "铜": 15.0, "铝": 8.0, "天然橡胶": 1.5},
    "机械密封报价":   {"不锈钢": 1.5, "天然橡胶": 0.3},
    "电机报价":       {"铜": 12.0, "铝": 6.0},
    "泵体铸件报价":   {"不锈钢": 60.0, "镍": 5.0},
    "叶轮报价":       {"不锈钢": 12.0, "镍": 1.5},
    "轴承报价":       {"不锈钢": 2.0},
    "法兰与标准件报价": {"不锈钢": 8.0},
    "外协加工报价":   {"不锈钢": 10.0, "镍": 1.0},
    "底座报价":       {"不锈钢": 150.0},
}

# ── Base processing cost per component (元) ──────────────────────────────────
# Fixed manufacturing cost that does NOT scale with material weight.
# Represents: precision grinding, lapping, specialized equipment, mould setup,
# quality testing, engineering overhead — the "you pay for the process, not the metal."
# Without this, precision components (seals, bearings) whose material cost is
# tiny (e.g. ¥26 worth of SS + rubber) would estimate at ¥100 instead of ¥800+.
COMPONENT_BASE_PROCESSING = {
    "整泵报价":       3500,
    "机械密封报价":    650,
    "电机报价":       1700,
    "泵体铸件报价":   2000,
    "叶轮报价":       1200,
    "轴承报价":        400,
    "法兰与标准件报价": 150,
    "外协加工报价":    700,
    "底座报价":        800,
}

# ── Default cost factors per component ──────────────────────────────────────
# process_factor: manufacturing complexity multiplier
# region_factor: regional labor cost adjustment
# brand_factor: brand premium multiplier
# profit_rate: typical supplier margin
COMPONENT_DEFAULT_FACTORS = {
    "整泵报价":       {"process_factor": 1.20, "region_factor": 1.05, "brand_factor": 1.15, "profit_rate": 0.18},
    "机械密封报价":   {"process_factor": 0.80, "region_factor": 1.05, "brand_factor": 1.10, "profit_rate": 0.25},
    "电机报价":       {"process_factor": 0.60, "region_factor": 1.05, "brand_factor": 1.10, "profit_rate": 0.15},
    "泵体铸件报价":   {"process_factor": 0.90, "region_factor": 1.05, "brand_factor": 1.10, "profit_rate": 0.20},
    "叶轮报价":       {"process_factor": 1.00, "region_factor": 1.05, "brand_factor": 1.10, "profit_rate": 0.22},
    "轴承报价":       {"process_factor": 0.50, "region_factor": 1.05, "brand_factor": 1.10, "profit_rate": 0.20},
    "法兰与标准件报价": {"process_factor": 0.40, "region_factor": 1.05, "brand_factor": 1.05, "profit_rate": 0.15},
    "外协加工报价":   {"process_factor": 0.70, "region_factor": 1.05, "brand_factor": 1.05, "profit_rate": 0.15},
    "底座报价":       {"process_factor": 0.30, "region_factor": 1.05, "brand_factor": 1.05, "profit_rate": 0.10},
}

# ── Sub-type selectors for components with significant spec-driven cost variance ──
# Multiplier is applied to base_processing only (materials don't change with spec).
COMPONENT_SUB_TYPES = {
    "电机报价": [
        {"label": "标准电机 (Y/Y2系列)", "multiplier": 1.0},
        {"label": "防爆电机 (隔爆/增安)", "multiplier": 1.8},
        {"label": "变频电机 (独立风机)", "multiplier": 1.5},
        {"label": "高效节能 (IE3/IE4)", "multiplier": 1.3},
    ],
    "机械密封报价": [
        {"label": "单端面标准", "multiplier": 1.0},
        {"label": "双端面", "multiplier": 1.6},
        {"label": "集装式", "multiplier": 2.0},
        {"label": "耐酸特种", "multiplier": 1.5},
    ],
}

# ── Industry benchmark price ranges per component (元) ──────────────────────
# Wide ranges reflecting small/standard → large/acid-resistant configurations.
MARKET_REFERENCE_RANGES = {
    "整泵报价":       (15000, 65000),
    "机械密封报价":   (1000, 5000),
    "电机报价":       (4000, 20000),
    "泵体铸件报价":   (6000, 30000),
    "叶轮报价":       (3000, 12000),
    "轴承报价":       (600, 4000),
    "法兰与标准件报价": (400, 3000),
    "外协加工报价":   (1500, 6000),
    "底座报价":       (3000, 15000),
}


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    region = db.Column(db.String(40), nullable=False)
    is_long_term = db.Column(db.Boolean, default=False)
    quality_rating = db.Column(db.Float, default=0)
    delivery_rating = db.Column(db.Float, default=0)
    rating_source_name = db.Column(db.String(160), default="")
    rating_source_url = db.Column(db.String(500), default="")
    rating_source_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    quotes = db.relationship("Quote", back_populates="supplier", cascade="all, delete-orphan")


class Quote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.String(160), nullable=False)
    product_name = db.Column(db.String(120), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    flow_rate = db.Column(db.Float, default=0)
    head = db.Column(db.Float, default=0)
    power = db.Column(db.Float, default=0)
    temperature = db.Column(db.Float, default=0)
    medium = db.Column(db.String(120), default="")
    ph_value = db.Column(db.Float, default=7)
    component_name = db.Column(db.String(80), nullable=False)
    material = db.Column(db.String(60), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    process = db.Column(db.String(80), nullable=False)
    brand = db.Column(db.String(60), nullable=False)
    origin = db.Column(db.String(40), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    supplier_quote = db.Column(db.Float, nullable=False)
    final_price = db.Column(db.Float, default=0)
    includes_tax = db.Column(db.Boolean, default=True)
    includes_freight = db.Column(db.Boolean, default=False)
    payment_terms = db.Column(db.String(160), default="")
    quote_date = db.Column(db.Date, nullable=False)
    quantity = db.Column(db.Integer, default=1)
    is_urgent = db.Column(db.Boolean, default=False)
    repaired = db.Column(db.Boolean, default=False)
    service_life = db.Column(db.Float, default=0)
    leaked = db.Column(db.Boolean, default=False)
    customer_feedback = db.Column(db.String(240), default="")

    quote_source_name = db.Column(db.String(160), default="")
    quote_source_url = db.Column(db.String(500), default="")
    quote_source_date = db.Column(db.Date)

    material_unit_price = db.Column(db.Float, default=0)
    material_source_name = db.Column(db.String(160), default="")
    material_source_url = db.Column(db.String(500), default="")
    material_source_date = db.Column(db.Date)

    material_trend_factor = db.Column(db.Float, default=1)
    trend_source_name = db.Column(db.String(160), default="")
    trend_source_url = db.Column(db.String(500), default="")
    trend_source_date = db.Column(db.Date)

    process_factor = db.Column(db.Float, default=0)
    process_source_name = db.Column(db.String(160), default="")
    process_source_url = db.Column(db.String(500), default="")
    process_source_date = db.Column(db.Date)

    region_factor = db.Column(db.Float, default=1)
    region_source_name = db.Column(db.String(160), default="")
    region_source_url = db.Column(db.String(500), default="")
    region_source_date = db.Column(db.Date)

    brand_factor = db.Column(db.Float, default=1)
    brand_source_name = db.Column(db.String(160), default="")
    brand_source_url = db.Column(db.String(500), default="")
    brand_source_date = db.Column(db.Date)

    quantity_factor_value = db.Column(db.Float, default=1)
    quantity_source_name = db.Column(db.String(160), default="")
    quantity_source_url = db.Column(db.String(500), default="")
    quantity_source_date = db.Column(db.Date)

    urgent_factor = db.Column(db.Float, default=1)
    urgent_source_name = db.Column(db.String(160), default="")
    urgent_source_url = db.Column(db.String(500), default="")
    urgent_source_date = db.Column(db.Date)

    profit_rate = db.Column(db.Float, default=0)
    profit_source_name = db.Column(db.String(160), default="")
    profit_source_url = db.Column(db.String(500), default="")
    profit_source_date = db.Column(db.Date)

    normal_threshold = db.Column(db.Float, default=0)
    critical_threshold = db.Column(db.Float, default=0)
    rule_source_name = db.Column(db.String(160), default="")
    rule_source_url = db.Column(db.String(500), default="")
    rule_source_date = db.Column(db.Date)

    material_cost = db.Column(db.Float, default=0)
    processing_cost = db.Column(db.Float, default=0)
    estimated_cost = db.Column(db.Float, default=0)
    reasonable_price = db.Column(db.Float, default=0)
    reference_price = db.Column(db.Float, default=0)
    historical_avg = db.Column(db.Float, default=0)
    deviation_rate = db.Column(db.Float, default=0)
    analysis_status = db.Column(db.String(40), default="")
    calculation_text = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    supplier = db.relationship("Supplier", back_populates="quotes")


class MarketSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_name = db.Column(db.String(80), nullable=False)
    exchange = db.Column(db.String(120), nullable=False)
    exchange_code = db.Column(db.String(20), nullable=False)
    contract_code = db.Column(db.String(40), default="")
    price = db.Column(db.Float, default=0)
    previous_price = db.Column(db.Float, default=0)
    change_rate = db.Column(db.Float, default=0)
    unit = db.Column(db.String(40), default="")
    quote_date = db.Column(db.Date)
    source_name = db.Column(db.String(160), default="")
    source_url = db.Column(db.String(500), default="")
    product_url = db.Column(db.String(500), default="")
    fetch_status = db.Column(db.String(40), default="")
    error_message = db.Column(db.String(300), default="")
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Price range helpers: exchange price + 200/300 markup ──────────────
    @property
    def price_range_low(self) -> float:
        """Exchange 最新价 + 200 (lower end of purchase quote range)."""
        return (self.price or 0) + 200

    @property
    def price_range_high(self) -> float:
        """Exchange 最新价 + 300 (upper end of purchase quote range)."""
        return (self.price or 0) + 300

    @property
    def price_per_gram(self) -> float:
        """元/克  (1 吨 = 1,000,000 克)"""
        return (self.price or 0) / 1_000_000

    @property
    def range_low_per_gram(self) -> float:
        return self.price_range_low / 1_000_000

    @property
    def range_high_per_gram(self) -> float:
        return self.price_range_high / 1_000_000


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            flash("请先登录系统")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def to_float(value: str | None, default: float = 0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def to_int(value: str | None, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def parse_required_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_quote_date(value: str | None) -> date:
    return parse_required_date(value) or date.today()


def valid_url(value: str | None) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_text(value: str | None) -> str:
    return (value or "").strip()


def source_groups() -> list[tuple[str, str]]:
    return [
        ("quote", "供应商报价/产品规格"),
        ("material", "材料单价"),
        ("trend", "原材料波动系数"),
        ("process", "工艺系数"),
        ("region", "地区成本系数"),
        ("brand", "品牌溢价系数"),
        ("quantity", "数量折扣系数"),
        ("urgent", "交期加急系数"),
        ("profit", "利润率"),
        ("rule", "判断阈值规则"),
    ]


def to_market_float(value) -> float:
    if value in [None, "", "-", "—"]:
        return 0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0


# ── Sina Finance fetch (works from any IP worldwide) ──────────────────────
# Sina mirrors SHFE futures in real-time.
# URL: https://hq.sinajs.cn/list=nf_CU2607,nf_AL2607,...
# Response per symbol:
#   var hq_str_nf_CU2607="铜2607,昨结算,今开盘,最高,最低,最新价,买价,买量,卖价,卖量,日期,时间,持仓";
# Field index (0-based): 0=名称 1=时间戳 2=今开盘 3=最高 4=最低 5=最新价 ... 10=昨结算

def fetch_sina_futures() -> tuple[dict[str, dict], str]:
    """
    Fetch all RAW_MATERIALS futures prices from Sina Finance in one request.
    Returns ({material_name: {price, previous_price, change_rate, source_url}}, error_msg).
    Works from Singapore and all non-China locations.
    """
    codes = ",".join(m["sina_code"] for m in RAW_MATERIALS)
    # Append cache-busting timestamp to avoid stale cached responses
    cache_buster = int(time())
    url = f"https://hq.sinajs.cn/list={codes}&_={cache_buster}"
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://finance.sina.com.cn/",
                "Accept": "*/*",
            },
        )
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            # Sina pages use GBK encoding
            for enc in ("gbk", "utf-8", "gb2312"):
                try:
                    text_body = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text_body = raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {}, f"Sina行情接口请求失败：{exc}"

    results: dict[str, dict] = {}
    for material in RAW_MATERIALS:
        code = material["sina_code"]
        match = re.search(
            rf'hq_str_{re.escape(code)}="([^"]*)"',
            text_body,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        fields = match.group(1).split(",")
        if len(fields) < 6:
            continue
        latest_price = to_market_float(fields[5])     # 最新价
        previous_price = to_market_float(fields[10])  # 昨结算
        if latest_price <= 0:
            latest_price = to_market_float(fields[2])  # fallback: 今开盘
        if latest_price <= 0:
            continue
        change_rate = (
            (latest_price - previous_price) / previous_price
            if previous_price > 0 else 0.0
        )
        results[material["name"]] = {
            "price": latest_price,
            "previous_price": previous_price,
            "change_rate": change_rate,
            "source_url": f"https://hq.sinajs.cn/list={code}",
            "sina_page": material["sina_page"],
        }
    if not results:
        return {}, "Sina行情接口返回数据为空，合约代码可能需要更新"
    return results, ""


def fetch_akshare_futures() -> tuple[dict[str, dict], str]:
    """
    Fallback: fetch futures prices via akshare (also uses Sina under the hood,
    but with a different request path that may succeed when direct calls fail).
    """
    if ak is None:
        return {}, "akshare 未安装，无法使用备用行情源"
    results: dict[str, dict] = {}
    for material in RAW_MATERIALS:
        code = material["exchange_code"].upper()
        try:
            # akshare symbol format: e.g. 'CU2607'
            symbol = f"{code}2607"
            df = ak.futures_zh_spot(symbol=symbol, market="CF", adjust="0")
            if df is None or df.empty:
                continue
            row = df.iloc[-1] if len(df) > 0 else None
            if row is None:
                continue
            # Column names vary by akshare version; try common patterns
            latest = 0.0
            prev = 0.0
            for col in df.columns:
                cl = str(col).lower()
                if "最新" in cl or "latest" in cl or "current" in cl:
                    latest = to_market_float(row[col])
                if "昨收" in cl or "昨结" in cl or "settle" in cl or "previous" in cl:
                    prev = to_market_float(row[col])
            if latest <= 0:
                continue
            change_rate = (latest - prev) / prev if prev > 0 else 0.0
            results[material["name"]] = {
                "price": latest,
                "previous_price": prev,
                "change_rate": change_rate,
                "source_url": f"https://hq.sinajs.cn/list={material['sina_code']}",
                "sina_page": material["sina_page"],
            }
        except Exception:
            continue
    if not results:
        return {}, "akshare 备用行情源也未返回数据"
    return results, ""


def update_market_snapshots() -> tuple[int, list[str]]:
    # 直接调用你文件里现成的完美函数，它已支持获取指定的 2607 延时合约！
    results, error_msg = fetch_sina_futures()
    if not results and ak is not None:
        # Sina direct failed, try akshare fallback
        results, fallback_err = fetch_akshare_futures()
        if not results:
            error_msg = f"Sina: {error_msg}；akshare: {fallback_err}"
        else:
            error_msg = ""  # fallback succeeded
    if not results:
        return 0, [error_msg or "行情数据获取失败，请检查网络或稍后重试"]

    success_count = 0
    errors = []

    for material in RAW_MATERIALS:
        name = material["name"]

        if name not in results:
            error_txt = f"{name}: 未能获取到行情数据"
            errors.append(error_txt)
            mark_market_failure(material, error_txt)
            continue

        data = results[name]

        # 只需要保存核心的基础数据到数据库，不要保存加价后的数据
        snapshot_data = {
            "material_name": name,
            "exchange": material["exchange"],
            "exchange_code": material["exchange_code"],
            "contract_code": material["target_contract"], # 这里会自动对应如 cu2607
            "price": data["price"],                       # 核心最新价
            "previous_price": data["previous_price"],
            "change_rate": data["change_rate"],
            "unit": material["unit"],
            "quote_date": date.today(),
            "source_name": "新浪期货",
            "source_url": data["sina_page"],
            "product_url": material["source_page"],
            "fetch_status": "成功",
            "error_message": "",
            "fetched_at": datetime.utcnow(),
        }

        # 使用你文件里已经有的插入/更新函数
        upsert_market_snapshot(snapshot_data)
        success_count += 1

    db.session.commit()
    return success_count, errors



def upsert_market_snapshot(snapshot_data: dict[str, object]) -> None:
    snapshot = MarketSnapshot.query.filter_by(
        material_name=snapshot_data["material_name"],
        exchange_code=snapshot_data["exchange_code"],
    ).first()
    if not snapshot:
        snapshot = MarketSnapshot(
            material_name=snapshot_data["material_name"],
            exchange=snapshot_data["exchange"],
            exchange_code=snapshot_data["exchange_code"],
        )
        db.session.add(snapshot)
    for key, value in snapshot_data.items():
        setattr(snapshot, key, value)


def mark_market_failure(material: dict[str, str], error: str) -> None:
    snapshot = MarketSnapshot.query.filter_by(
        material_name=material["name"],
        exchange_code=material["exchange_code"],
    ).first()
    if not snapshot:
        snapshot = MarketSnapshot(
            material_name=material["name"],
            exchange=material["exchange"],
            exchange_code=material["exchange_code"],
        )
        db.session.add(snapshot)
    snapshot.unit = material["unit"]
    snapshot.product_url = material["source_page"]
    snapshot.fetch_status = "失败"
    snapshot.error_message = error
    snapshot.fetched_at = datetime.utcnow()


def latest_market_snapshots() -> list[MarketSnapshot]:
    snapshots = {
        snapshot.material_name: snapshot
        for snapshot in MarketSnapshot.query.order_by(MarketSnapshot.material_name.asc()).all()
    }
    rows = []
    for material in RAW_MATERIALS:
        rows.append(snapshots.get(material["name"]) or MarketSnapshot(
            material_name=material["name"],
            exchange=material["exchange"],
            exchange_code=material["exchange_code"],
            unit=material["unit"],
            product_url=material["source_page"],
            fetch_status="未更新",
        ))
    return rows


def component_market_cards() -> list[dict[str, object]]:
    snapshots = {snapshot.material_name: snapshot for snapshot in latest_market_snapshots()}
    cards = []
    for component, drivers in COMPONENT_DRIVERS.items():
        driver_rows = [snapshots[name] for name in drivers if name in snapshots]
        available = [row for row in driver_rows if (row.price or 0) > 0 and row.source_url]
        avg_change = mean([row.change_rate for row in available]) if available else 0
        cards.append(
            {
                "component": component,
                "drivers": driver_rows,
                "available_count": len(available),
                "avg_change": avg_change,
            }
        )
    return cards


# ── Component Price Analysis Engine ─────────────────────────────────────────
def get_live_price_map() -> dict[str, float]:
    """Return {material_name: latest_futures_price_per_ton} from MarketSnapshot."""
    snapshots = MarketSnapshot.query.filter(
        MarketSnapshot.price > 0,
        MarketSnapshot.fetch_status == "成功",
    ).all()
    return {s.material_name: s.price for s in snapshots}


def get_component_history(component_name: str, limit: int = 5) -> list[Quote]:
    """Return recent quotes for a component for market reference."""
    return (
        Quote.query
        .filter_by(component_name=component_name)
        .order_by(Quote.quote_date.desc())
        .limit(limit)
        .all()
    )


def calculate_component_price(
    component_name: str,
    live_prices: dict[str, float],
    bom_override: dict[str, float] | None = None,
    process_factor: float | None = None,
    region_factor: float | None = None,
    brand_factor: float | None = None,
    profit_rate: float | None = None,
    base_proc_override: float | None = None,
    sub_type: str | None = None,
    special_surcharge: float = 0.0,
) -> dict:
    """
    Estimate a component price from live futures data and a bill of materials.

    Formula (v3 — base processing × sub-type + variable + surcharge):
      material_cost       = SUM(weight_kg × futures_price_per_ton / 1000)
      effective_base_proc = base_processing × sub_type_multiplier
      variable_processing = material_cost × process_factor
      total_processing    = effective_base_proc + variable_processing + special_surcharge
      adjusted_cost       = (material_cost + total_processing) × region_factor × brand_factor
      estimated_price     = adjusted_cost × (1 + profit_rate)
    """
    defaults = COMPONENT_DEFAULT_FACTORS.get(component_name, {})
    bom = COMPONENT_BOM.get(component_name, {})
    raw_base_proc = base_proc_override if base_proc_override is not None else COMPONENT_BASE_PROCESSING.get(component_name, 0)

    # Resolve sub-type multiplier
    sub_multiplier = 1.0
    sub_label = ""
    sub_types = COMPONENT_SUB_TYPES.get(component_name, [])
    if sub_type and sub_types:
        for st in sub_types:
            if st["label"] == sub_type:
                sub_multiplier = st["multiplier"]
                sub_label = st["label"]
                break
    elif sub_types:
        # Default: first option
        sub_multiplier = sub_types[0]["multiplier"]
        sub_label = sub_types[0]["label"]

    base_proc = raw_base_proc * sub_multiplier

    effective_bom = dict(bom)
    if bom_override:
        effective_bom.update(bom_override)

    pf = process_factor if process_factor is not None else defaults.get("process_factor", 1.0)
    rf = region_factor if region_factor is not None else defaults.get("region_factor", 1.05)
    bf = brand_factor if brand_factor is not None else defaults.get("brand_factor", 1.10)
    pr = profit_rate if profit_rate is not None else defaults.get("profit_rate", 0.15)

    breakdown: list[dict] = []
    total_material = 0.0

    for mat_name, weight_kg in effective_bom.items():
        price_per_ton = live_prices.get(mat_name, 0.0)
        price_per_kg = price_per_ton / 1000.0
        subtotal = weight_kg * price_per_kg
        total_material += subtotal
        breakdown.append({
            "material": mat_name,
            "weight_kg": weight_kg,
            "price_per_ton": price_per_ton,
            "price_per_kg": price_per_kg,
            "subtotal": subtotal,
        })

    variable_processing = total_material * pf
    total_processing = base_proc + variable_processing + special_surcharge
    adjusted_cost = (total_material + total_processing) * rf * bf
    estimated_price = adjusted_cost * (1.0 + pr)

    calc_lines = []
    for item in breakdown:
        calc_lines.append(
            f"  {item['material']}: {item['weight_kg']:.1f}kg × {item['price_per_kg']:.2f}元/kg"
            f" = {item['subtotal']:.2f}元"
        )
    calc_lines.append(f"材料成本合计: {total_material:.2f}元")
    if sub_label:
        calc_lines.append(f"基础加工费: {raw_base_proc:.0f} × {sub_label}(×{sub_multiplier:.1f}) = {base_proc:.2f}元")
    else:
        calc_lines.append(f"基础加工费（固定）: {base_proc:.2f}元")
    calc_lines.append(f"浮动加工费: {total_material:.2f} × {pf:.2f} = {variable_processing:.2f}元")
    if special_surcharge > 0:
        calc_lines.append(f"特殊要求附加费: {special_surcharge:.2f}元")
    calc_lines.append(f"加工成本合计: {base_proc:.2f} + {variable_processing:.2f}" +
                      (f" + {special_surcharge:.2f}" if special_surcharge > 0 else "") +
                      f" = {total_processing:.2f}元")
    calc_lines.append(
        f"调整后成本: ({total_material:.2f} + {total_processing:.2f})"
        f" × {rf:.2f} × {bf:.2f} = {adjusted_cost:.2f}元"
    )
    calc_lines.append(f"估算价格: {adjusted_cost:.2f} × (1 + {pr:.2f}) = {estimated_price:.2f}元")

    return {
        "material_breakdown": breakdown,
        "total_material_cost": total_material,
        "base_processing": base_proc,
        "raw_base_processing": raw_base_proc,
        "sub_type_label": sub_label,
        "sub_multiplier": sub_multiplier,
        "variable_processing": variable_processing,
        "special_surcharge": special_surcharge,
        "processing_cost": total_processing,
        "adjusted_cost": adjusted_cost,
        "estimated_price": estimated_price,
        "calculation_text": "\n".join(calc_lines),
        "used_factors": {"process_factor": pf, "region_factor": rf, "brand_factor": bf, "profit_rate": pr},
        "sub_types": sub_types,
    }


def component_price_analysis_context(
    selected_component: str = "整泵报价",
    bom_overrides: dict[str, float] | None = None,
    factor_overrides: dict[str, float] | None = None,
    base_proc_override: float | None = None,
    sub_type: str | None = None,
    special_surcharge: float = 0.0,
) -> dict:
    """Build full context for the Component Price Analysis dashboard section."""
    live_prices = get_live_price_map()
    all_summaries: list[dict] = []
    for comp in COMPONENT_OPTIONS:
        fov = None
        bov = None
        if comp == selected_component:
            fov = factor_overrides
            bov = bom_overrides
        result = calculate_component_price(
            comp, live_prices,
            bom_override=bov,
            process_factor=fov.get("process_factor") if fov else None,
            region_factor=fov.get("region_factor") if fov else None,
            brand_factor=fov.get("brand_factor") if fov else None,
            profit_rate=fov.get("profit_rate") if fov else None,
            base_proc_override=base_proc_override if comp == selected_component else None,
            sub_type=sub_type if comp == selected_component else None,
            special_surcharge=special_surcharge if comp == selected_component else 0.0,
        )
        ref_range = MARKET_REFERENCE_RANGES.get(comp, (0, 0))
        history = get_component_history(comp, limit=3)
        hist_prices = [q.final_price or q.supplier_quote for q in history if q.quote_source_url]
        all_summaries.append({
            "component": comp,
            "estimated_price": result["estimated_price"],
            "market_low": ref_range[0],
            "market_high": ref_range[1],
            "historical_avg": mean(hist_prices) if hist_prices else 0.0,
            "history_count": len(hist_prices),
        })

    selected_analysis = calculate_component_price(
        selected_component, live_prices,
        bom_override=bom_overrides,
        process_factor=factor_overrides.get("process_factor") if factor_overrides else None,
        region_factor=factor_overrides.get("region_factor") if factor_overrides else None,
        brand_factor=factor_overrides.get("brand_factor") if factor_overrides else None,
        profit_rate=factor_overrides.get("profit_rate") if factor_overrides else None,
        base_proc_override=base_proc_override,
        sub_type=sub_type,
        special_surcharge=special_surcharge,
    )
    ref_range = MARKET_REFERENCE_RANGES.get(selected_component, (0, 0))
    selected_history = get_component_history(selected_component, limit=5)

    return {
        "selected_component": selected_component,
        "component_options": COMPONENT_OPTIONS,
        "all_summaries": all_summaries,
        "selected_analysis": selected_analysis,
        "market_range_low": ref_range[0],
        "market_range_high": ref_range[1],
        "historical_quotes": selected_history,
        "bom": COMPONENT_BOM.get(selected_component, {}),
        "default_factors": COMPONENT_DEFAULT_FACTORS.get(selected_component, {}),
        "live_prices": live_prices,
    }


def status_from_deviation(deviation: float, normal_threshold: float, critical_threshold: float) -> str:
    if deviation >= critical_threshold:
        return "严重偏高"
    if deviation >= normal_threshold:
        return "偏高"
    if deviation <= -critical_threshold:
        return "明显偏低"
    if deviation <= -normal_threshold:
        return "偏低"
    return "合理"


def estimate_quote(payload: dict[str, object]) -> dict[str, float | str]:
    material_cost = (
        payload["weight"] * payload["material_unit_price"] * payload["material_trend_factor"]
    )
    processing_cost = material_cost * payload["process_factor"]
    estimated_cost = (
        (material_cost + processing_cost)
        * payload["region_factor"]
        * payload["brand_factor"]
        * payload["quantity_factor_value"]
        * payload["urgent_factor"]
    )
    reasonable_price = estimated_cost * (1 + payload["profit_rate"])
    calculation_text = (
        f"材料成本 = {payload['weight']:.2f} × {payload['material_unit_price']:.2f} × "
        f"{payload['material_trend_factor']:.4f} = {material_cost:.2f}\n"
        f"加工成本 = {material_cost:.2f} × {payload['process_factor']:.4f} = {processing_cost:.2f}\n"
        f"调整后成本 = ({material_cost:.2f} + {processing_cost:.2f}) × "
        f"{payload['region_factor']:.4f} × {payload['brand_factor']:.4f} × "
        f"{payload['quantity_factor_value']:.4f} × {payload['urgent_factor']:.4f} = {estimated_cost:.2f}\n"
        f"合理报价 = {estimated_cost:.2f} × (1 + {payload['profit_rate']:.4f}) = {reasonable_price:.2f}"
    )
    return {
        "material_cost": material_cost,
        "processing_cost": processing_cost,
        "estimated_cost": estimated_cost,
        "reasonable_price": reasonable_price,
        "reference_price": reasonable_price,
        "calculation_text": calculation_text,
    }


def matching_history(query: Quote | dict[str, object], exclude_id: int | None = None) -> list[Quote]:
    model = query["model"] if isinstance(query, dict) else query.model
    component_name = query["component_name"] if isinstance(query, dict) else query.component_name
    material = query["material"] if isinstance(query, dict) else query.material
    quote_query = Quote.query.filter(
        Quote.model == model,
        Quote.component_name == component_name,
        Quote.material == material,
    )
    if exclude_id:
        quote_query = quote_query.filter(Quote.id != exclude_id)
    return quote_query.order_by(Quote.quote_date.desc()).all()


def analyze_quote_payload(payload: dict[str, object], exclude_id: int | None = None) -> dict[str, object]:
    estimate = estimate_quote(payload)
    history = matching_history(payload, exclude_id)
    historical_prices = [item.final_price or item.supplier_quote for item in history if item.quote_source_url]
    historical_avg = mean(historical_prices) if historical_prices else 0
    reference_price = estimate["reference_price"]
    supplier_quote = payload["supplier_quote"]
    deviation_rate = (supplier_quote - reference_price) / reference_price if reference_price else 0
    status = status_from_deviation(
        deviation_rate,
        payload["normal_threshold"],
        payload["critical_threshold"],
    )
    return {
        **estimate,
        "historical_avg": historical_avg,
        "deviation_rate": deviation_rate,
        "status": status,
        "history_count": len(history),
    }


def get_or_create_supplier(form) -> Supplier:
    supplier_name = clean_text(form.get("supplier_name"))
    supplier = Supplier.query.filter_by(name=supplier_name).first()
    if not supplier:
        supplier = Supplier(name=supplier_name, region=form["region"])
        db.session.add(supplier)
    supplier.region = form["region"]
    supplier.is_long_term = bool(form.get("is_long_term"))
    supplier.quality_rating = to_float(form.get("quality_rating"))
    supplier.delivery_rating = to_float(form.get("delivery_rating"))
    supplier.rating_source_name = clean_text(form.get("rating_source_name"))
    supplier.rating_source_url = clean_text(form.get("rating_source_url"))
    supplier.rating_source_date = parse_required_date(form.get("rating_source_date"))
    return supplier


def build_quote_payload(form, supplier: Supplier) -> dict[str, object]:
    payload = {
        "created_by": session.get("user_email", ""),
        "product_name": clean_text(form.get("product_name")),
        "model": clean_text(form.get("model")),
        "flow_rate": to_float(form.get("flow_rate")),
        "head": to_float(form.get("head")),
        "power": to_float(form.get("power")),
        "temperature": to_float(form.get("temperature")),
        "medium": clean_text(form.get("medium")),
        "ph_value": to_float(form.get("ph_value"), 7),
        "component_name": form["component_name"],
        "material": form["material"],
        "weight": to_float(form.get("weight")),
        "process": form["process"],
        "brand": form["brand"],
        "origin": form["origin"],
        "supplier_id": supplier.id,
        "supplier_quote": to_float(form.get("supplier_quote")),
        "final_price": to_float(form.get("final_price")),
        "includes_tax": bool(form.get("includes_tax")),
        "includes_freight": bool(form.get("includes_freight")),
        "payment_terms": clean_text(form.get("payment_terms")),
        "quote_date": parse_quote_date(form.get("quote_date")),
        "quantity": max(to_int(form.get("quantity"), 1), 1),
        "is_urgent": bool(form.get("is_urgent")),
        "repaired": bool(form.get("repaired")),
        "service_life": to_float(form.get("service_life")),
        "leaked": bool(form.get("leaked")),
        "customer_feedback": clean_text(form.get("customer_feedback")),
        "material_unit_price": to_float(form.get("material_unit_price")),
        "material_trend_factor": to_float(form.get("material_trend_factor"), 1),
        "process_factor": to_float(form.get("process_factor")),
        "region_factor": to_float(form.get("region_factor"), 1),
        "brand_factor": to_float(form.get("brand_factor"), 1),
        "quantity_factor_value": to_float(form.get("quantity_factor_value"), 1),
        "urgent_factor": to_float(form.get("urgent_factor"), 1),
        "profit_rate": to_float(form.get("profit_rate")),
        "normal_threshold": to_float(form.get("normal_threshold")),
        "critical_threshold": to_float(form.get("critical_threshold")),
    }
    for key, _label in source_groups():
        payload[f"{key}_source_name"] = clean_text(form.get(f"{key}_source_name"))
        payload[f"{key}_source_url"] = clean_text(form.get(f"{key}_source_url"))
        payload[f"{key}_source_date"] = parse_required_date(form.get(f"{key}_source_date"))
    return payload


def validate_payload(payload: dict[str, object], supplier: Supplier) -> list[str]:
    errors = []
    required_text = [
        ("产品名称", payload["product_name"]),
        ("型号", payload["model"]),
        ("供应商名称", supplier.name),
    ]
    for label, value in required_text:
        if not value:
            errors.append(f"{label}不能为空")

    positive_fields = [
        ("重量", payload["weight"]),
        ("供应商报价", payload["supplier_quote"]),
        ("材料单价", payload["material_unit_price"]),
        ("原材料波动系数", payload["material_trend_factor"]),
        ("地区成本系数", payload["region_factor"]),
        ("品牌溢价系数", payload["brand_factor"]),
        ("数量折扣系数", payload["quantity_factor_value"]),
        ("交期加急系数", payload["urgent_factor"]),
    ]
    for label, value in positive_fields:
        if value <= 0:
            errors.append(f"{label}必须大于 0")

    if payload["process_factor"] < 0:
        errors.append("工艺系数不能小于 0")
    if payload["profit_rate"] < 0:
        errors.append("利润率不能小于 0")
    if payload["normal_threshold"] <= 0:
        errors.append("合理偏差阈值必须大于 0")
    if payload["critical_threshold"] <= payload["normal_threshold"]:
        errors.append("严重偏差阈值必须大于合理偏差阈值")
    if supplier.quality_rating <= 0 or supplier.delivery_rating <= 0:
        errors.append("历史质量评价和历史交期评价必须大于 0")

    for key, label in source_groups():
        if not payload[f"{key}_source_name"]:
            errors.append(f"{label}来源名称不能为空")
        if not valid_url(payload[f"{key}_source_url"]):
            errors.append(f"{label}来源链接必须是 http 或 https 链接")
        if not payload[f"{key}_source_date"]:
            errors.append(f"{label}来源日期不能为空")

    if not supplier.rating_source_name:
        errors.append("供应商评价来源名称不能为空")
    if not valid_url(supplier.rating_source_url):
        errors.append("供应商评价来源链接必须是 http 或 https 链接")
    if not supplier.rating_source_date:
        errors.append("供应商评价来源日期不能为空")
    return errors


def create_quote_from_payload(payload: dict[str, object]) -> Quote:
    analysis = analyze_quote_payload(payload)
    quote = Quote(
        **payload,
        material_cost=analysis["material_cost"],
        processing_cost=analysis["processing_cost"],
        estimated_cost=analysis["estimated_cost"],
        reasonable_price=analysis["reasonable_price"],
        reference_price=analysis["reference_price"],
        historical_avg=analysis["historical_avg"],
        deviation_rate=analysis["deviation_rate"],
        analysis_status=analysis["status"],
        calculation_text=analysis["calculation_text"],
    )
    db.session.add(quote)
    return quote


def quote_source_complete(quote: Quote) -> bool:
    for key, _label in source_groups():
        if not getattr(quote, f"{key}_source_url", ""):
            return False
    return True


def source_completeness(quotes: list[Quote]) -> float:
    if not quotes:
        return 0
    complete_count = sum(1 for quote in quotes if quote_source_complete(quote))
    return complete_count / len(quotes)


def supplier_rows() -> list[dict[str, object]]:
    rows = []
    for supplier in Supplier.query.order_by(Supplier.name.asc()).all():
        quotes = supplier.quotes
        if not quotes:
            continue
        rows.append(
            {
                "supplier": supplier,
                "quote_count": len(quotes),
                "avg_price": mean([quote.supplier_quote for quote in quotes]),
                "abnormal_count": sum(1 for quote in quotes if quote.analysis_status != "合理"),
                "repair_count": sum(1 for quote in quotes if quote.repaired or quote.leaked),
            }
        )
    return rows


def dashboard_context(form_data=None, preview=None, errors=None):
    quotes = Quote.query.order_by(Quote.quote_date.desc(), Quote.id.desc()).all()
    status_counts = {
        "合理": sum(1 for item in quotes if item.analysis_status == "合理"),
        "偏高": sum(1 for item in quotes if item.analysis_status in ["偏高", "严重偏高"]),
        "偏低": sum(1 for item in quotes if item.analysis_status in ["偏低", "明显偏低"]),
    }
    total_quote = sum(item.supplier_quote for item in quotes)
    latest_alerts = [
        item
        for item in quotes
        if item.analysis_status in ["偏高", "严重偏高", "偏低", "明显偏低"]
    ][:6]
    return {
        "quotes": quotes[:12],
        "all_quotes": quotes,
        "supplier_rows": supplier_rows(),
        "market_snapshots": latest_market_snapshots(),
        "component_market_cards": component_market_cards(),
        "latest_alerts": latest_alerts,
        "status_counts": status_counts,
        "total_quotes": len(quotes),
        "avg_quote": total_quote / len(quotes) if quotes else 0,
        "source_completeness": source_completeness(quotes),
        "form_data": form_data or {},
        "preview": preview,
        "errors": errors or [],
        "material_options": MATERIAL_OPTIONS,
        "process_options": PROCESS_OPTIONS,
        "region_options": REGION_OPTIONS,
        "brand_options": BRAND_OPTIONS,
        "component_options": COMPONENT_OPTIONS,
        "today": date.today().isoformat(),
        "current_email": session.get("user_email", "") if has_request_context() else "",
    }


@app.template_filter("money")
def money(value: float | int | None) -> str:
    return f"{float(value or 0):,.2f}"


@app.template_filter("money0")
def money0(value: float | int | None) -> str:
    return f"{float(value or 0):,.0f}"


@app.template_filter("percent")
def percent(value: float | int | None) -> str:
    return f"{float(value or 0) * 100:.2f}%"


@app.template_filter("per_gram")
def per_gram(value: float | int | None) -> str:
    """Convert 元/吨 → 元/克 (divides by 1,000,000)."""
    return f"{float(value or 0) / 1_000_000:.4f}"


@app.template_filter("format_gram")
def format_gram(value: float | int | None) -> str:
    """Format an already-converted 元/克 value with 4 decimal places (no conversion)."""
    return f"{float(value or 0):.4f}"


@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if not email.endswith(ALLOWED_DOMAIN):
            flash("仅允许使用公司邮箱登录")
            return redirect(url_for("login"))
        session["user_email"] = email
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        action = request.form.get("action")
        supplier = get_or_create_supplier(request.form)
        db.session.flush()
        payload = build_quote_payload(request.form, supplier)
        errors = validate_payload(payload, supplier)
        if errors:
            db.session.rollback()
            return render_template("dashboard.html", **dashboard_context(request.form, errors=errors))
        if action == "preview":
            preview = analyze_quote_payload(payload)
            db.session.rollback()
            return render_template("dashboard.html", **dashboard_context(request.form, preview))
        quote = create_quote_from_payload(payload)
        db.session.commit()
        flash(f"已保存 {quote.supplier.name} 的报价，系统判断为：{quote.analysis_status}")
        return redirect(url_for("dashboard"))
    # GET: build component price analysis from query params
    selected_component = request.args.get("analysis_component", "整泵报价")
    bom_overrides: dict[str, float] = {}
    factor_overrides: dict[str, float] = {}
    base_proc_override: float | None = None
    for key in request.args:
        if key.startswith("bom_"):
            material_name = key[4:]
            try:
                bom_overrides[material_name] = float(request.args[key])
            except (ValueError, TypeError):
                pass
    for param in ("pf", "rf", "bf", "pr"):
        if param in request.args:
            try:
                factor_overrides[param] = float(request.args[param])
            except (ValueError, TypeError):
                pass
    if "base_proc" in request.args:
        try:
            base_proc_override = float(request.args["base_proc"])
        except (ValueError, TypeError):
            pass
    sub_type = request.args.get("sub_type", "")
    special_surcharge = 0.0
    if "surcharge" in request.args:
        try:
            special_surcharge = float(request.args["surcharge"])
        except (ValueError, TypeError):
            pass

    analysis_context = component_price_analysis_context(
        selected_component=selected_component,
        bom_overrides=bom_overrides or None,
        factor_overrides={
            "process_factor": factor_overrides.get("pf"),
            "region_factor": factor_overrides.get("rf"),
            "brand_factor": factor_overrides.get("bf"),
            "profit_rate": factor_overrides.get("pr"),
        } if factor_overrides else None,
        base_proc_override=base_proc_override,
        sub_type=sub_type or None,
        special_surcharge=special_surcharge,
    )

    ctx = dashboard_context()
    ctx["analysis_context"] = analysis_context
    return render_template("dashboard.html", **ctx)


@app.route("/market/update", methods=["POST"])
@login_required
def update_market():
    start = time()
    success_count, errors = update_market_snapshots()
    elapsed = time() - start
    if success_count:
        flash(f"已更新 {success_count} 个原材料行情（新浪财经·上交所），用时 {elapsed:.1f} 秒")
    else:
        flash("行情更新失败：" + "；".join(errors[:2]))
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:quote_id>", methods=["POST"])
@login_required
def delete_quote(quote_id: int):
    quote = Quote.query.get_or_404(quote_id)
    db.session.delete(quote)
    db.session.commit()
    flash("报价记录已删除")
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


def ensure_columns(table_name: str, columns: dict[str, str]) -> None:
    existing = {
        row[1]
        for row in db.session.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()
    }
    for column_name, column_type in columns.items():
        if column_name not in existing:
            db.session.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def migrate_database() -> None:
    ensure_columns(
        "supplier",
        {
            "rating_source_name": "VARCHAR(160)",
            "rating_source_url": "VARCHAR(500)",
            "rating_source_date": "DATE",
        },
    )
    ensure_columns(
        "quote",
        {
            "quote_source_name": "VARCHAR(160)",
            "quote_source_url": "VARCHAR(500)",
            "quote_source_date": "DATE",
            "material_unit_price": "FLOAT DEFAULT 0",
            "material_source_name": "VARCHAR(160)",
            "material_source_url": "VARCHAR(500)",
            "material_source_date": "DATE",
            "material_trend_factor": "FLOAT DEFAULT 1",
            "trend_source_name": "VARCHAR(160)",
            "trend_source_url": "VARCHAR(500)",
            "trend_source_date": "DATE",
            "process_factor": "FLOAT DEFAULT 0",
            "process_source_name": "VARCHAR(160)",
            "process_source_url": "VARCHAR(500)",
            "process_source_date": "DATE",
            "region_factor": "FLOAT DEFAULT 1",
            "region_source_name": "VARCHAR(160)",
            "region_source_url": "VARCHAR(500)",
            "region_source_date": "DATE",
            "brand_factor": "FLOAT DEFAULT 1",
            "brand_source_name": "VARCHAR(160)",
            "brand_source_url": "VARCHAR(500)",
            "brand_source_date": "DATE",
            "quantity_factor_value": "FLOAT DEFAULT 1",
            "quantity_source_name": "VARCHAR(160)",
            "quantity_source_url": "VARCHAR(500)",
            "quantity_source_date": "DATE",
            "urgent_factor": "FLOAT DEFAULT 1",
            "urgent_source_name": "VARCHAR(160)",
            "urgent_source_url": "VARCHAR(500)",
            "urgent_source_date": "DATE",
            "profit_rate": "FLOAT DEFAULT 0",
            "profit_source_name": "VARCHAR(160)",
            "profit_source_url": "VARCHAR(500)",
            "profit_source_date": "DATE",
            "normal_threshold": "FLOAT DEFAULT 0",
            "critical_threshold": "FLOAT DEFAULT 0",
            "rule_source_name": "VARCHAR(160)",
            "rule_source_url": "VARCHAR(500)",
            "rule_source_date": "DATE",
            "material_cost": "FLOAT DEFAULT 0",
            "processing_cost": "FLOAT DEFAULT 0",
            "historical_avg": "FLOAT DEFAULT 0",
            "calculation_text": "TEXT",
        },
    )


def remove_demo_seed_data() -> None:
    demo_quotes = Quote.query.filter_by(created_by="seed@danaipumps.cn").all()
    for quote in demo_quotes:
        db.session.delete(quote)
    db.session.flush()
    demo_names = ["江苏恒耐泵业", "浙江华耐装备", "山东鲁泵机械", "广东南科流体"]
    for supplier in Supplier.query.filter(Supplier.name.in_(demo_names)).all():
        if not supplier.quotes:
            db.session.delete(supplier)
    db.session.commit()


with app.app_context():
    db.create_all()
    migrate_database()
    remove_demo_seed_data()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
