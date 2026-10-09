"""Clean ground-truth world: catalog, customers, FX table and orders.

Everything messy is derived from this later, so this module never injects defects.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..money import money
from ..text import ascii_fold

LOCAL_TZ = ZoneInfo("Europe/Ljubljana")
YEAR = 2025

SHOP_HOURS = (8, 20)
WAREHOUSE_HOURS = (7, 16)
ALL_DAY = (0, 24)

WALK_IN_SHARE = 0.3  # till sales with no customer attached
PICKUP_SHARE = 0.12  # Slovenian web orders collected in store
WHOLESALE_DISCOUNT = Decimal("0.85")

CATALOG = {
    "BRK": ("Brakes", ["Disc Brake Pads", "Hydraulic Caliper", "Rotor 160mm", "Rotor 180mm", "V-Brake Set",
                       "Brake Cable Kit"]),
    "CHN": ("Chains", ["11-Speed Chain", "12-Speed Chain", "Single Speed Chain", "Quick Link 5-pack", "Chain Checker"]),
    "TYR": ("Tyres", ["Road Tyre 700x28", "Gravel Tyre 700x40", "MTB Tyre 29x2.4", "Inner Tube 700c",
                      "Tubeless Sealant 500ml"]),
    "SDL": ("Saddles", ["Road Saddle Carbon", "Comfort Saddle Gel", "Gravel Saddle", "Kids Saddle"]),
    "LGT": ("Lights", ["Front Light 800lm", "Rear Light USB", "Light Set Commuter", "Helmet Light"]),
    "TLS": ("Tools", ["Multi-Tool 18", "Torque Wrench", "Floor Pump", "Mini Pump", "Tyre Levers"]),
    "HLM": ("Helmets", ["Road Helmet Aero", "MTB Helmet Trail", "Urban Helmet", "Kids Helmet"]),
}

FIRST = ["Žiga", "Luka", "Nika", "Ana", "Matej", "Špela", "Tjaša", "Jure", "Rok", "Maša", "Ivana", "Matija",
         "Đorđe", "Lena", "Jakob", "Sophie", "Felix", "Hannah", "Oliver", "Emily", "James", "Chloe", "Noah",
         "Mia", "Lukas", "Anja", "Tomaž", "Urška"]
LAST = ["Novak", "Horvat", "Krajnc", "Kovačič", "Zupančič", "Potočnik", "Mlakar", "Vidmar", "Golob", "Jurić",
        "Kovačević", "Babić", "Müller", "Gruber", "Huber", "Wagner", "Smith", "Taylor", "Brown", "Wilson",
        "Johnson", "Petrović", "Černe"]
EMAIL_DOMAINS = ["gmail.com", "siol.net", "outlook.com", "gmx.at", "proton.me"]

BUSINESSES = ["Kolesarstvo Rog", "Bike Center Maribor", "Alpe Cycles", "Velo Graz", "Biciklistički Centar Zagreb",
              "Trail Shop Bled", "Pedal & Co", "Kolo Servis Celje", "Gravel House Koper", "Radhaus Klagenfurt",
              "MTB Pohorje", "Urban Wheels Ljubljana", "Bici Trieste", "Spica Sport", "Hribi Bikes", "Tirol Rad",
              "Istria Bike", "Kranj Kolo", "Soča Outdoor", "Panonija Cikel"]
BUSINESS_COUNTRIES = ["SI", "SI", "SI", "AT", "HR", "IT"]
LEGAL_SUFFIX = {"SI": "d.o.o.", "AT": "GmbH", "HR": "d.o.o.", "IT": "S.r.l."}

# Repeated entries weight the draw. The till mostly sees locals; the webshop sells further afield.
POS_COUNTRIES = ["SI"] * 8 + ["AT", "HR"]
WEB_COUNTRIES = ["SI"] * 4 + ["AT"] * 2 + ["HR", "DE", "IT", "GB", "GB", "US", "US"]
CURRENCY_BY_COUNTRY = {"GB": "GBP", "US": "USD"}
BASE_FX = {"USD": Decimal("1.08"), "GBP": Decimal("0.85")}


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    price_eur: Decimal


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    email: str
    country: str
    is_business: bool


@dataclass
class Line:
    sku: str
    qty: int
    unit_price: Decimal

    @property
    def total(self) -> Decimal:
        return money(self.unit_price * self.qty)


@dataclass
class Order:
    channel: str
    native_id: str
    customer: Customer | None
    placed_at: datetime  # tz-aware UTC
    currency: str
    lines: list[Line]
    pickup_at: datetime | None = None  # click-and-collect web orders get rung through the till on pickup
    pos_receipt: str | None = None  # the till's receipt number for that pickup

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.native_id}"

    @property
    def total(self) -> Decimal:
        return sum((ln.total for ln in self.lines), Decimal("0.00"))

    @property
    def collect_in_store(self) -> bool:
        return self.pickup_at is not None


@dataclass
class World:
    products: list[Product]
    customers: list[Customer]
    fx: dict[tuple[int, str], Decimal]  # (month, currency) -> units per 1 EUR
    orders: list[Order] = field(default_factory=list)

    def rate(self, currency: str, when: datetime) -> Decimal:
        """Units of `currency` per euro in the local month of `when`."""
        if currency == "EUR":
            return Decimal(1)
        return self.fx[(when.astimezone(LOCAL_TZ).month, currency)]

    def total_eur(self, order: Order) -> Decimal:
        return money(order.total / self.rate(order.currency, order.placed_at))


# Reference data

def build_products(rng: random.Random) -> list[Product]:
    products = []
    for prefix, (category, names) in CATALOG.items():
        numbers = sorted(rng.sample(range(1, 400), len(names)))
        for number, name in zip(numbers, names, strict=True):
            price = money(Decimal(rng.randint(400, 25000)) / 100)
            products.append(Product(f"{prefix}-{number:04d}", name, category, price))
    return products


def build_consumers(rng: random.Random, n: int) -> list[Customer]:
    customers: list[Customer] = []
    emails: set[str] = set()
    while len(customers) < n:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{ascii_fold(first)}.{ascii_fold(last)}@{rng.choice(EMAIL_DOMAINS)}".lower()
        if email in emails:
            continue
        emails.add(email)
        country = rng.choice(POS_COUNTRIES + WEB_COUNTRIES)
        customers.append(Customer(f"C{len(customers) + 1:04d}", f"{first} {last}", email, country, False))
    return customers


def build_businesses(rng: random.Random) -> list[Customer]:
    customers = []
    for i, base in enumerate(BUSINESSES, start=1):
        country = rng.choice(BUSINESS_COUNTRIES)
        domain = ascii_fold(base).lower().replace("&", "").replace(" ", "")
        name = f"{base} {LEGAL_SUFFIX[country]}"
        customers.append(Customer(f"B{i:03d}", name, f"orders@{domain}.com", country, True))
    return customers


def build_fx(rng: random.Random) -> dict[tuple[int, str], Decimal]:
    """A gentle monthly random walk from each base rate."""
    fx = {}
    for currency, rate in BASE_FX.items():
        for month in range(1, 13):
            rate = (rate * Decimal(1 + rng.uniform(-0.02, 0.02))).quantize(Decimal("0.0001"))
            fx[(month, currency)] = rate
    return fx


# Orders

def random_day(rng: random.Random) -> date:
    return date(YEAR, 1, 1) + timedelta(days=rng.randrange(365))


def local_moment(rng: random.Random, day: date, hours: tuple[int, int]) -> datetime:
    """A random minute within opening hours on `day`, local time, returned as UTC."""
    local = datetime(day.year, day.month, day.day, rng.randrange(*hours), rng.randrange(60), tzinfo=LOCAL_TZ)
    return local.astimezone(UTC)


def pick_lines(rng: random.Random, world: World, currency: str, when: datetime, n: int,
               qty: tuple[int, int], discount: Decimal = Decimal(1)) -> list[Line]:
    rate = world.rate(currency, when)
    return [Line(p.sku, rng.randint(*qty), money(p.price_eur * discount * rate))
            for p in rng.sample(world.products, n)]


def pos_orders(rng: random.Random, world: World, n: int) -> list[Order]:
    regulars = [c for c in world.customers if not c.is_business and c.country in POS_COUNTRIES]
    orders = []
    for _ in range(n):
        when = local_moment(rng, random_day(rng), SHOP_HOURS)
        customer = None if rng.random() < WALK_IN_SHARE else rng.choice(regulars)
        # Receipt numbers come later, once pickups are interleaved (see number_receipts).
        lines = pick_lines(rng, world, "EUR", when, rng.randint(1, 3), (1, 3))
        orders.append(Order("pos", "", customer, when, "EUR", lines))
    return orders


def web_orders(rng: random.Random, world: World, n: int) -> list[Order]:
    consumers = [c for c in world.customers if not c.is_business]
    orders = []
    web_id = 100000
    for _ in range(n):
        web_id += rng.randint(1, 7)
        customer = rng.choice(consumers)
        currency = CURRENCY_BY_COUNTRY.get(customer.country, "EUR")
        when = local_moment(rng, random_day(rng), ALL_DAY)
        lines = pick_lines(rng, world, currency, when, rng.randint(1, 4), (1, 2))
        order = Order("web", str(web_id), customer, when, currency, lines)
        if customer.country == "SI" and rng.random() < PICKUP_SHARE:
            pickup_day = when.astimezone(LOCAL_TZ).date() + timedelta(days=rng.randint(1, 3))
            order.pickup_at = local_moment(rng, pickup_day, SHOP_HOURS)
        orders.append(order)
    return orders


def wholesale_orders(rng: random.Random, world: World, n: int) -> list[Order]:
    businesses = [c for c in world.customers if c.is_business]
    orders = []
    for i in range(1, n + 1):
        when = local_moment(rng, random_day(rng), WAREHOUSE_HOURS)
        lines = pick_lines(rng, world, "EUR", when, rng.randint(3, 8), (10, 120), WHOLESALE_DISCOUNT)
        orders.append(Order("wholesale", f"WH-{i:04d}", rng.choice(businesses), when, "EUR", lines))
    return orders


def number_receipts(rng: random.Random, orders: list[Order]) -> None:
    """Till receipt numbers run in time order across walk-in sales and pickups alike."""
    visits = [(o.placed_at, o) for o in orders if o.channel == "pos"]
    visits += [(o.pickup_at, o) for o in orders if o.pickup_at]
    receipt = 1000
    for _, order in sorted(visits, key=lambda v: v[0]):
        receipt += rng.randint(1, 3)
        if order.channel == "pos":
            order.native_id = f"R-{receipt}"
        else:
            order.pos_receipt = f"R-{receipt}"


def build_world(seed: int = 7, n_pos: int = 450, n_web: int = 650, n_wholesale: int = 120) -> World:
    rng = random.Random(seed)
    customers = build_consumers(rng, 260) + build_businesses(rng)
    world = World(build_products(rng), customers, build_fx(rng))
    world.orders = (pos_orders(rng, world, n_pos)
                    + web_orders(rng, world, n_web)
                    + wholesale_orders(rng, world, n_wholesale))
    world.orders.sort(key=lambda o: o.placed_at)
    number_receipts(rng, world.orders)
    return world
