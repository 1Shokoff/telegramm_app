from __future__ import annotations

import logging
from pathlib import Path

from aiogram.types import BufferedInputFile
from aiohttp import web

from . import (
    auth,
    catalog,
    const,
    db,
    instruction as instruction_mod,
    legal,
    pages,
    texts,
    udid as udid_mod,
)
from .config import Config
from .service import OrderService, ServiceError

log = logging.getLogger(__name__)
routes = web.RouteTableDef()

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


def asset_version() -> str:
    """Метка версии для ссылок на app.js и styles.css.

    Без неё Telegram на телефоне может показать файлы из своего кэша —
    после выкатки покупатель увидит старый экран.
    """
    newest = 0.0
    for name in ("app.js", "styles.css", "index.html"):
        path = WEBAPP_DIR / name
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return "%x" % int(newest)


RECEIPT_LIMIT = 10 * 1024 * 1024
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
RECEIPT_TYPES = PHOTO_TYPES + ("application/pdf",)


def requisites_public(cfg: Config) -> list[dict]:
    """Реквизиты для витрины: к строкам добавляем адрес иконки."""
    icons = catalog.icon_files()
    rows = []
    for row in cfg.requisites:
        icon = row["icon"]
        if icon == "sbp":
            url = "/static/brand/sbp.svg"
        elif icon and icon in icons:
            url = "/static/icons/" + icons[icon]
        else:
            url = ""
        rows.append(dict(row, iconUrl=url))
    return rows


def about_block(cfg: Config) -> dict:
    """Реквизиты, контакты и документы — их показывают витрина и подвал."""
    return {
        "legalName": cfg.legal_name,
        "inn": cfg.legal_inn,
        "address": cfg.legal_address,
        "email": cfg.contact_email,
        "phone": cfg.contact_phone,
        "legalLine": cfg.legal_line,
        "docs": [
            {"key": key, "title": title, "hint": hint, "url": cfg.doc_path(key)}
            for key, title, hint in const.DOCUMENTS
        ],
    }


def bot_url(request: web.Request) -> str:
    """Ссылка на бота для гостя, который открыл витрину в браузере."""
    cfg = cfg_of(request)
    username = request.app.get("bot_username") or cfg.bot_username
    return ("https://t.me/%s" % username) if username else ""


def cfg_of(request: web.Request) -> Config:
    return request.app["cfg"]


def service_of(request: web.Request) -> OrderService:
    return request.app["service"]


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def current_user(request: web.Request) -> auth.WebAppUser:
    cfg = cfg_of(request)
    init_data = request.headers.get("X-Init-Data", "")

    if not init_data and cfg.dev_mode:
        dev_id = request.headers.get("X-Dev-User") or "1"
        return auth.WebAppUser(id=int(dev_id), first_name="Dev", username="dev", language_code="ru")

    try:
        return auth.check_init_data(init_data, cfg.bot_token, cfg.init_data_ttl)
    except auth.AuthError as exc:
        raise ApiError("Не удалось подтвердить вход: %s" % exc, status=401)


async def require_seller(request: web.Request) -> auth.WebAppUser:
    user = await current_user(request)
    if not cfg_of(request).is_seller(user.id):
        raise ApiError("Недоступно", status=403)
    return user


async def body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except Exception:
        raise ApiError("Ожидался JSON")
    return data if isinstance(data, dict) else {}


async def with_user(order: dict | None) -> dict | None:
    """db.get_order не джойнит пользователя — добираем имя для карточки продавца."""
    if not order or "username" in order:
        return order
    user = await db.get_user(order["user_id"]) or {}
    merged = dict(order)
    merged["username"] = user.get("username")
    merged["first_name"] = user.get("first_name")
    return merged


def order_public(order: dict | None, *, full: bool = False) -> dict | None:
    if not order:
        return None
    data = {
        "id": order["id"],
        "code": order["code"],
        "status": order["status"],
        "statusTitle": const.TITLES.get(order["status"], order["status"]),
        "hint": texts.HINTS.get(order["status"], ""),
        "step": const.STEP.get(order["status"], 0),
        "price": order["price_rub"],
        "priceText": texts.money(order["price_rub"]),
        "paymentMode": order["payment_mode"],
        "app": order.get("app_slug"),
        "productName": catalog.product_name(order.get("app_slug")),
        "udidMasked": udid_mod.mask(order.get("device_udid")),
        "hasUdid": bool(order.get("device_udid")),
        # instruction — примечание продавца к шаблону, может быть пустым.
        "instruction": order.get("instruction"),
        "instructionReady": order["status"] == const.DONE,
        "note": order.get("seller_note"),
        "createdAt": order["created_at"],
        "updatedAt": order["updated_at"],
        "isOpen": order["status"] in const.OPEN_STATUSES,
    }
    if full:
        data["udid"] = order.get("device_udid")
        data["userId"] = order["user_id"]
        data["username"] = order.get("username")
        data["firstName"] = order.get("first_name")
        data["paymentRef"] = order.get("payment_ref")
    return data


def order_id_of(data: dict) -> int:
    """Мусор в orderId — это ошибка запроса, а не падение сервера."""
    try:
        return int(data.get("orderId", 0))
    except (TypeError, ValueError):
        raise ApiError("Некорректный orderId")


def message_public(message: dict, viewer: str) -> dict:
    return {
        "id": message["id"],
        "author": message["author"],
        "text": message["text"],
        "createdAt": message["created_at"],
        "mine": message["author"] == viewer,
    }


def viewer_role(request: web.Request, user: auth.WebAppUser) -> str:
    return db.SELLER if cfg_of(request).is_seller(user.id) else db.BUYER


# ------------------------------------------------------------------ маршруты


@routes.get("/health")
async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@routes.get("/api/bootstrap")
async def bootstrap(request: web.Request) -> web.Response:
    cfg = cfg_of(request)
    user = await current_user(request)
    await db.upsert_user(user.id, user.username, user.first_name)
    order = await db.get_active_order(user.id)

    return web.json_response(
        {
            "user": {
                "id": user.id,
                "firstName": user.first_name,
                "username": user.username,
                "isSeller": cfg.is_seller(user.id),
            },
            "product": {
                "title": cfg.product_title,
                "subtitle": cfg.product_subtitle,
                "price": cfg.price_rub,
                "priceText": texts.money(cfg.price_rub),
                "appsCount": catalog.count(),
            },
            "payment": {
                "mode": cfg.payment_mode,
                "details": cfg.payment_details,
                "stars": cfg.price_stars,
                "requisites": requisites_public(cfg),
                # Пусто — витрина покажет цену заказа.
                "amount": cfg.pay_amount,
            },
            "catalog": [
                dict(item, priceText=texts.money(item["price"]))
                for item in catalog.public_catalog(cfg.price_rub)
            ],
            "categories": list(catalog.CATEGORIES),
            "featured": list(catalog.FEATURED),
            "steps": list(const.STEP_NAMES),
            "support": cfg.support_username,
            "about": about_block(cfg),
            # Шаблон инструкции: витрина показывает его, когда продавец отправил.
            "instruction": {
                "steps": instruction_mod.public_steps(),
                "bots": instruction_mod.bot_links(),
            },
            "privacy": texts.PRIVACY,
            "testUdid": udid_mod.TEST_UDID if cfg.dev_mode else None,
            "order": order_public(order),
            "unread": await db.unread_count(order["id"], db.BUYER) if order else 0,
            "sellerUnread": (
                sum((await db.unread_by_order(db.SELLER)).values())
                if cfg.is_seller(user.id)
                else 0
            ),
        }
    )


@routes.get("/api/public")
async def public_bootstrap(request: web.Request) -> web.Response:
    """Витрина для гостя без Telegram: каталог, цены, документы, реквизиты.

    Без этого модерация платёжной системы не может открыть магазин —
    Mini App пускает только по подписи initData.
    """
    cfg = cfg_of(request)
    return web.json_response(
        {
            "public": True,
            "user": {"isSeller": False},
            "order": None,
            "product": {
                "title": cfg.product_title,
                "subtitle": cfg.product_subtitle,
                "price": cfg.price_rub,
                "priceText": texts.money(cfg.price_rub),
                "appsCount": catalog.count(),
            },
            # Реквизиты перевода гостю не показываем: они нужны только в заказе.
            "payment": {
                "mode": cfg.payment_mode, "details": "",
                "stars": cfg.price_stars, "requisites": [],
            },
            "catalog": [
                dict(item, priceText=texts.money(item["price"]))
                for item in catalog.public_catalog(cfg.price_rub)
            ],
            "categories": list(catalog.CATEGORIES),
            "featured": list(catalog.FEATURED),
            "steps": list(const.STEP_NAMES),
            "support": cfg.support_username,
            "about": about_block(cfg),
            "privacy": texts.PRIVACY,
            "botUrl": bot_url(request),
        }
    )


@routes.get("/api/doc/{key}")
async def api_doc(request: web.Request) -> web.Response:
    """Текст документа для витрины — открывается внутри приложения."""
    key = request.match_info["key"]
    found = legal.document(key, cfg_of(request))
    if not found:
        raise ApiError("Документ не найден", status=404)
    title, html = found
    return web.json_response({"key": key, "title": title, "html": html})


@routes.get("/api/order")
async def get_order(request: web.Request) -> web.Response:
    user = await current_user(request)
    order = await db.get_active_order(user.id)
    return web.json_response({"order": order_public(order)})


@routes.post("/api/order/create")
async def create_order(request: web.Request) -> web.Response:
    user = await current_user(request)
    await db.upsert_user(user.id, user.username, user.first_name)
    data = await body(request)
    app_slug = data.get("app") or None
    if app_slug is not None and not isinstance(app_slug, str):
        raise ApiError("Некорректное приложение")
    order, _created = await service_of(request).get_or_create_order(user.id, app_slug)
    return web.json_response({"order": order_public(order)})


@routes.post("/api/order/claim")
async def claim(request: web.Request) -> web.Response:
    user = await current_user(request)
    data = await body(request)
    order = await service_of(request).claim_payment(order_id_of(data), user.id)
    return web.json_response({"order": order_public(order)})


@routes.post("/api/order/receipt")
async def upload_receipt(request: web.Request) -> web.Response:
    """Чек об оплате из витрины: отдаём его Telegram, у себя файл не храним."""
    user = await current_user(request)
    try:
        order_id = int(request.query.get("orderId", 0))
    except ValueError:
        raise ApiError("Некорректный orderId")

    try:
        field = await (await request.multipart()).next()
    except Exception:
        raise ApiError("Ожидался файл")
    if field is None or field.name != "file":
        raise ApiError("Ожидался файл")

    content_type = (field.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type not in RECEIPT_TYPES:
        raise ApiError("Подойдёт картинка или PDF")

    data = bytearray()
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > RECEIPT_LIMIT:
            raise ApiError("Файл больше 10 МБ — пришлите поменьше")
    if not data:
        raise ApiError("Файл пустой")

    name = field.filename or ("чек.pdf" if content_type == "application/pdf" else "чек.jpg")
    file = BufferedInputFile(bytes(data), filename=name)
    await service_of(request).attach_receipt(
        order_id, user.id, file, is_photo=content_type in PHOTO_TYPES, title=name
    )
    return web.json_response({"ok": True, "name": name})


@routes.post("/api/order/demopay")
async def demopay(request: web.Request) -> web.Response:
    cfg = cfg_of(request)
    if cfg.payment_mode != "demo":
        raise ApiError("Демо-оплата выключена")
    user = await current_user(request)
    data = await body(request)
    order_id = order_id_of(data)
    order = await db.get_order(order_id)
    if not order or order["user_id"] != user.id:
        raise ApiError("Это не ваш заказ", status=403)
    updated = await service_of(request).confirm_payment(order_id, "demo", ref="demo")
    return web.json_response({"order": order_public(updated)})


@routes.post("/api/order/invoice")
async def invoice(request: web.Request) -> web.Response:
    cfg = cfg_of(request)
    if cfg.payment_mode != "stars":
        raise ApiError("Оплата Stars выключена")
    from .handlers import payments

    user = await current_user(request)
    data = await body(request)
    order = await db.get_order(order_id_of(data))
    if not order or order["user_id"] != user.id:
        raise ApiError("Это не ваш заказ", status=403)
    link = await payments.create_invoice_link(request.app["bot"], cfg, order)
    return web.json_response({"link": link})


@routes.post("/api/order/udid")
async def submit_udid(request: web.Request) -> web.Response:
    user = await current_user(request)
    data = await body(request)
    order = await service_of(request).submit_udid(
        order_id_of(data), str(data.get("udid", "")), user.id
    )
    return web.json_response({"order": order_public(order)})


@routes.get("/api/chat")
async def get_chat(request: web.Request) -> web.Response:
    user = await current_user(request)
    role = viewer_role(request, user)
    try:
        order_id = int(request.query.get("orderId", 0))
    except (TypeError, ValueError):
        raise ApiError("Некорректный orderId")

    messages = await service_of(request).read_chat(order_id, user.id, role == db.SELLER)
    return web.json_response(
        {"role": role, "messages": [message_public(m, role) for m in messages]}
    )


@routes.post("/api/chat")
async def post_chat(request: web.Request) -> web.Response:
    user = await current_user(request)
    role = viewer_role(request, user)
    data = await body(request)
    message = await service_of(request).post_message(
        order_id_of(data), str(data.get("text", "")), user.id, role == db.SELLER
    )
    return web.json_response({"message": message_public(message, role)})


async def notify_payload(user_id: int, is_seller: bool, order: dict | None) -> dict:
    prefs = await db.get_notify_prefs(user_id)
    kinds = [
        {"key": key, "title": title, "hint": hint, "enabled": prefs.get(key, True)}
        for key, title, hint in const.notifications_for(is_seller)
    ]
    order_block = None
    if order:
        override = await db.get_order_notify(user_id, order["id"])
        mode = const.ORDER_NOTIFY_DEFAULT
        if override is True:
            mode = const.ORDER_NOTIFY_ON
        elif override is False:
            mode = const.ORDER_NOTIFY_OFF
        order_block = {"id": order["id"], "code": order["code"], "mode": mode}
    return {
        "kinds": kinds,
        "order": order_block,
        "modes": [
            {"key": key, "title": title} for key, title in const.ORDER_NOTIFY_TITLES.items()
        ],
    }


@routes.get("/api/notify")
async def get_notify(request: web.Request) -> web.Response:
    user = await current_user(request)
    is_seller = cfg_of(request).is_seller(user.id)

    order = None
    raw_order = request.query.get("orderId")
    if raw_order:
        try:
            order = await db.get_order(int(raw_order))
        except (TypeError, ValueError):
            raise ApiError("Некорректный orderId")
        if order and not is_seller and order["user_id"] != user.id:
            raise ApiError("Это не ваш заказ", status=403)
    elif not is_seller:
        order = await db.get_active_order(user.id)

    return web.json_response(await notify_payload(user.id, is_seller, order))


@routes.post("/api/notify")
async def set_notify(request: web.Request) -> web.Response:
    user = await current_user(request)
    is_seller = cfg_of(request).is_seller(user.id)
    data = await body(request)

    kind = str(data.get("kind", ""))
    allowed = {key for key, _t, _h in const.notifications_for(is_seller)}
    if kind not in allowed:
        raise ApiError("Неизвестный вид уведомления: %s" % kind)

    await db.set_notify_pref(user.id, kind, bool(data.get("enabled")))
    return web.json_response(await notify_payload(user.id, is_seller, None))


@routes.post("/api/notify/order")
async def set_notify_order(request: web.Request) -> web.Response:
    user = await current_user(request)
    is_seller = cfg_of(request).is_seller(user.id)
    data = await body(request)

    order = await db.get_order(order_id_of(data))
    if not order:
        raise ApiError("Заказ не найден")
    if not is_seller and order["user_id"] != user.id:
        raise ApiError("Это не ваш заказ", status=403)

    mode = str(data.get("mode", const.ORDER_NOTIFY_DEFAULT))
    if mode not in const.ORDER_NOTIFY_TITLES:
        raise ApiError("Неизвестный режим: %s" % mode)

    enabled = {const.ORDER_NOTIFY_DEFAULT: None, const.ORDER_NOTIFY_ON: True, const.ORDER_NOTIFY_OFF: False}[mode]
    await db.set_order_notify(user.id, order["id"], enabled)
    return web.json_response(await notify_payload(user.id, is_seller, order))


@routes.post("/api/order/cancel")
async def cancel(request: web.Request) -> web.Response:
    user = await current_user(request)
    data = await body(request)
    order = await service_of(request).cancel(
        order_id_of(data), "user:%d" % user.id, by_seller=False, actor_id=user.id
    )
    return web.json_response({"order": order_public(order)})


@routes.post("/api/order/help")
async def order_help(request: web.Request) -> web.Response:
    user = await current_user(request)
    data = await body(request)
    order = await db.get_order(order_id_of(data))
    if not order or order["user_id"] != user.id:
        raise ApiError("Это не ваш заказ", status=403)
    await service_of(request).push_order_to_sellers(
        order, "🆘 <b>Покупатель просит помощь</b>", const.NOTIFY_HELP
    )
    return web.json_response({"ok": True})


@routes.post("/api/order/udid/check")
async def check_udid(request: web.Request) -> web.Response:
    """Проверка номера до отправки — как «Проверить номер» на экране UDID."""
    await current_user(request)
    data = await body(request)
    value, error = udid_mod.validate(str(data.get("udid", "")))
    return web.json_response({"valid": bool(value), "error": error, "masked": udid_mod.mask(value)})


# ------------------------------------------------------------------ продавец


@routes.get("/api/seller/orders")
async def seller_orders(request: web.Request) -> web.Response:
    await require_seller(request)
    status = request.query.get("status") or "open"
    query = (request.query.get("q") or "").strip() or None
    # Поиск идёт по всем заказам: искать код внутри одного фильтра бессмысленно.
    if query:
        status = "all"
    orders = await db.list_orders(status=None if status == "all" else status, query=query, limit=100)
    counts = await db.status_counts()
    unread = await db.unread_by_order(db.SELLER)

    payload = []
    for order in orders:
        item = order_public(order, full=True)
        assert item is not None
        item["unread"] = unread.get(order["id"], 0)
        payload.append(item)

    return web.json_response(
        {
            "orders": payload,
            "counts": counts,
            "titles": const.TITLES,
            "unreadTotal": sum(unread.values()),
        }
    )


@routes.post("/api/seller/action")
async def seller_action(request: web.Request) -> web.Response:
    user = await require_seller(request)
    data = await body(request)
    action = str(data.get("action", ""))
    order_id = order_id_of(data)
    actor = "seller:%d" % user.id
    svc = service_of(request)

    if action == "payok":
        order = await svc.confirm_payment(order_id, actor, ref=data.get("ref"))
    elif action == "payno":
        order = await svc.reject_payment(order_id, actor)
    elif action == "installed":
        order = await svc.mark_installed(order_id, actor)
    elif action == "instruction":
        order = await svc.send_instruction(order_id, str(data.get("text", "")), actor)
    elif action == "cancel":
        order = await svc.cancel(order_id, actor, by_seller=True)
    elif action == "note":
        await db.set_note(order_id, str(data.get("text", "")))
        order = await db.get_order(order_id)
    else:
        raise ApiError("Неизвестное действие: %s" % action)

    return web.json_response({"order": order_public(await with_user(order), full=True)})


# ------------------------------------------------------------------- статика


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    # Страницу не кэшируем, а ссылки внутри неё помечаем версией: иначе
    # клиент Telegram может месяцами показывать старую витрину.
    html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html.replace("__V__", asset_version()),
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@routes.get("/docs")
async def docs_index(request: web.Request) -> web.Response:
    return web.Response(
        text=pages.documents_page(cfg_of(request), asset_version()),
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@routes.get("/docs/{key}")
async def docs_page(request: web.Request) -> web.Response:
    html = pages.document_page(request.match_info["key"], cfg_of(request), asset_version())
    if html is None:
        raise web.HTTPNotFound(text="Документ не найден", content_type="text/plain")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
    except ServiceError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("Ошибка обработки %s", request.path)
        return web.json_response({"ok": False, "error": "Внутренняя ошибка"}, status=500)


def build_app(cfg: Config, bot, service: OrderService) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["cfg"] = cfg
    app["bot"] = bot
    app["service"] = service
    app.add_routes(routes)
    app.router.add_static("/static/", WEBAPP_DIR, name="static", show_index=False)
    return app
