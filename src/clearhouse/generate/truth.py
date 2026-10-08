"""Clean ground-truth world: catalog, customers, FX table and orders.

Everything messy is derived from this later, so this module never injects defects.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")
LOCAL_TZ = ZoneInfo("Europe/Ljubljana")
YEAR = 2025
WHOLESALE_DISCOUNT = Decimal("0.85")


def money(x: Decimal | int | str) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


CATALOG = {
    "BRK": ("Brakes", ["Disc Brake Pads", "Hydraulic Caliper", "Rotor 160mm", "Rotor 180mm", "V-Brake Set", "Brake Cable Kit"]),
    "CHN": ("Chains", ["11-Speed Chain", "12-Speed Chain", "Single Speed Chain", "Quick Link 5-pack", "Chain Checker"]),
    "TYR": ("Tyres", ["Road Tyre 700x28", "Gravel Tyre 700x40", "MTB Tyre 29x2.4", "Inner Tube 700c", "Tubeless Sealant 500ml"]),
    "SDL": ("Saddles", ["Road Saddle Carbon", "Comfort Saddle Gel", "Gravel Saddle", "Kids Saddle"]),
    "LGT": ("Lights", ["Front Light 800lm", "Rear Light USB", "Light Set Commuter", "Helmet Light"]),
    "TLS": ("Tools", ["Multi-Tool 18", "Torque Wrench", "Floor Pump", "Mini Pump", "Tyre Levers"]),
    "HLM": ("Helmets", ["Road Helmet Aero", "MTB Helmet Trail", "Urban Helmet", "Kids Helmet"]),
}

FIRST = ["Žiga", "Luka", "Nika", "Ana", "Matej", "Špela", "Tjaša", "Jure", "Rok", "Maša", "Ivana", "Matija", "Đorđe", "Lena",
         "Jakob", "Sophie", "Felix", "Hannah", "Oliver", "Emily", "James", "Chloe", "Noah", "Mia", "Lukas", "Anja", "Tomaž", "Urška"]
LAST = ["Novak", "Horvat", "Krajnc", "Kovačič", "Zupančič", "Potočnik", "Mlakar", "Vidmar", "Golob", "Jurić", "Kovačević",
        "Babić", "Müller", "Gruber", "Huber", "Wagner", "Smith", "Taylor", "Brown", "Wilson", "Johnson", "Petrović", "Černe"]
BUSINESSES = ["Kolesarstvo Rog", "Bike Center Maribor", "Alpe Cycles", "Velo Graz", "Biciklistički Centar Zagreb", "Trail Shop Bled",
              "Pedal & Co", "Kolo Servis Celje", "Gravel House Koper", "Radhaus Klagenfurt", "MTB Pohorje", "Urban Wheels Ljubljana",
              "Bici Trieste", "Spica Sport", "Hribi Bikes", "Tirol Rad", "Istria Bike", "Kranj Kolo", "Soča Outdoor", "Panonija Cikel"]
SUFFIX = {"SI": "d.o.o.", "AT": "GmbH", "HR": "d.o.o.", "IT": "S.r.l."}

# Consumer country mix per channel. The till only sees people who walk in.
POS_COUNTRIES = ["SI"] * 8 + ["AT", "HR"]
WEB_COUNTRIES = ["SI"] * 4 + ["AT"] * 2 + ["HR", "DE", "IT", "GB", "GB", "US", "US"]
CURRENCY_BY_COUNTRY = {"GB": "GBP", "US": "USD"}

BASE_FX = {"USD": Decimal("1.08"), "GBP": Decimal("0.85")}


def ascii_fold(s: str) -> str:
    s = s.replace("Đ", "D").replace("đ", "d")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


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
    placed_at: datetime  # always tz-aware UTC
    currency: str
    lines: list[Line]
    collect_in_store: bool = False
    pos_receipt: str | None = None  # set when a web order also gets rung through the till

    @property
    def key(self) -> str:
        return f"{self.channel}:{self.native_id}"

    @property
    def total(self) -> Decimal:
        return sum((ln.total for ln in self.lines), Decimal("0.00"))


@dataclass
class World:
    products: list[Product]
    customers: list[Customer]
    fx: dict[tuple[int, str], Decimal]  # (month, currency) -> units per 1 EUR
    orders: list[Order] = field(default_factory=list)

    def product(self, sku: str) -> Product:
        return self._by_sku[sku]

    def __post_init__(self) -> None:
        self._by_sku = {p.sku: p for p in self.products}

    def total_eur(self, order: Order) -> Decimal:
        if order.currency == "EUR":
            return order.total
        rate = self.fx[(order.placed_at.astimezone(LOCAL_TZ).month, order.currency)]
        return money(order.total / rate)


def build_products(rng: random.Random) -> list[Product]:
    products = []
    for prefix, (category, names) in CATALOG.items():
        numbers = rng.sample(range(1, 400), len(names))
        for n, name in zip(sorted(numbers), names):
            price = money(Decimal(rng.randint(400, 25000)) / 100)
            products.append(Product(f"{prefix}-{n:04d}", name, category, price))
    return products


def build_customers(rng: random.Random, consumers: int = 260) -> list[Customer]:
    customers: list[Customer] = []
    used_emails: set[str] = set()
    pool = POS_COUNTRIES + WEB_COUNTRIES
    while len(customers) < consumers:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        local = f"{ascii_fold(first)}.{ascii_fold(last)}".lower()
        email = f"{local}@{rng.choice(['gmail.com', 'siol.net', 'outlook.com', 'gmx.at', 'proton.me'])}"
        if email in used_emails:
            email = email.replace("@", f"{rng.randint(1, 99)}@")
            if email in used_emails:
                continue
        used_emails.add(email)
        customers.append(Customer(f"C{len(customers) + 1:04d}", f"{first} {last}", email, rng.choice(pool), False))
    for i, base in enumerate(BUSINESSES):
        country = rng.choice(["SI", "SI", "SI", "AT", "HR", "IT"])
        domain = ascii_fold(base).lower().replace(" & ", "").replace(" ", "") + ".com"
        customers.append(Customer(f"B{i + 1:03d}", f"{base} {SUFFIX[country]}", f"orders@{domain}", country, True))
    return customers


def build_fx(rng: random.Random) -> dict[tuple[int, str], Decimal]:
    fx = {}
    for cur, base in BASE_FX.items():
        rate = base
        for month in range(1, 13):
            rate = (rate * Decimal(1 + rng.uniform(-0.02, 0.02))).quantize(Decimal("0.0001"))
            fx[(month, cur)] = rate
    return fx


def random_moment(rng: random.Random, open_hour: int = 8, close_hour: int = 20) -> datetime:
    day = datetime(YEAR, 1, 1) + timedelta(days=rng.randrange(365))
    local = day.replace(hour=rng.randrange(open_hour, close_hour), minute=rng.randrange(60), tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc)


def price_in(world: World, product: Product, currency: str, when: datetime, wholesale: bool = False) -> Decimal:
    price = product.price_eur * (WHOLESALE_DISCOUNT if wholesale else 1)
    if currency != "EUR":
        price *= world.fx[(when.astimezone(LOCAL_TZ).month, currency)]
    return money(price)


def pick_lines(rng: random.Random, world: World, currency: str, when: datetime, n: int,
               qty: tuple[int, int], wholesale: bool = False) -> list[Line]:
    return [Line(p.sku, rng.randint(*qty), price_in(world, p, currency, when, wholesale))
            for p in rng.sample(world.products, n)]


def build_world(seed: int = 7, pos_orders: int = 450, web_orders: int = 650, wholesale_orders: int = 120) -> World:
    rng = random.Random(seed)
    world = World(build_products(rng), build_customers(rng), {})
    world.fx = build_fx(rng)

    consumers = [c for c in world.customers if not c.is_business]
    locals_ = [c for c in consumers if c.country in POS_COUNTRIES]
    businesses = [c for c in world.customers if c.is_business]

    for _ in range(pos_orders):
        when = random_moment(rng)
        # About a third of till sales are anonymous walk-ins. That's legitimate, not a defect.
        customer = rng.choice(locals_) if rng.random() > 0.3 else None
        world.orders.append(Order("pos", "", customer, when, "EUR",
                                  pick_lines(rng, world, "EUR", when, rng.randint(1, 3), (1, 3))))

    web_id = 100000
    for _ in range(web_orders):
        web_id += rng.randint(1, 7)
        customer = rng.choice(consumers)
        currency = CURRENCY_BY_COUNTRY.get(customer.country, "EUR")
        when = random_moment(rng, 0, 24)
        order = Order("web", str(web_id), customer, when, currency,
                      pick_lines(rng, world, currency, when, rng.randint(1, 4), (1, 2)))
        order.collect_in_store = customer.country == "SI" and currency == "EUR" and rng.random() < 0.12
        world.orders.append(order)

    for i in range(wholesale_orders):
        when = random_moment(rng, 7, 16)
        world.orders.append(Order("wholesale", f"WH-{i + 1:04d}", rng.choice(businesses), when, "EUR",
                                  pick_lines(rng, world, "EUR", when, rng.randint(3, 8), (10, 120), wholesale=True)))

    world.orders.sort(key=lambda o: o.placed_at)
    number_receipts(rng, world)
    return world


def pickup_time(order: Order) -> datetime:
    """Click-and-collect orders get rung through the till when picked up, a day or so later."""
    days = int(order.native_id) % 3 + 1
    return order.placed_at + timedelta(days=days, hours=(int(order.native_id) % 5))


def number_receipts(rng: random.Random, world: World) -> None:
    """Till receipt numbers run in time order across both walk-in sales and pickups."""
    events = [(o.placed_at, o) for o in world.orders if o.channel == "pos"]
    events += [(pickup_time(o), o) for o in world.orders if o.collect_in_store]
    receipt = 1000
    for _, order in sorted(events, key=lambda e: e[0]):
        receipt += rng.randint(1, 3)
        if order.channel == "pos":
            order.native_id = f"R-{receipt}"
        else:
            order.pos_receipt = f"R-{receipt}"
