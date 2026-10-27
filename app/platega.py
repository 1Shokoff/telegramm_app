"""Клиент Platega — платёжная страница со СБП, картами и SberPay.

Документация: https://docs.platega.io (оглавление для чтения — /llms.txt).

- POST /v2/transaction/process — транзакция и ссылка на страницу оплаты.
  Способ оплаты выбирает покупатель на странице. Ссылка живёт 15 минут.
- GET /transaction/{id} — статус: PENDING, CONFIRMED, CANCELED, CHARGEBACKED.
- Уведомление (callback) Platega шлёт на адрес из личного кабинета
  («Настройки → Callback URL») с теми же заголовками X-MerchantId и X-Secret.
  Подписи у него нет, поэтому статус после уведомления перепроверяем запросом.

Своя обёртка на aiohttp, а не SDK Platega: два запроса, лишняя зависимость
в образе ни к чему.
"""
from __future__ import annotations

import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

log = logging.getLogger(__name__)

PROVIDER = "platega"
PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
CANCELED = "CANCELED"
CHARGEBACKED = "CHARGEBACKED"
PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
# Деньги ушли обратно покупателю — целиком или частью.
REFUNDS = (CHARGEBACKED, PARTIALLY_REFUNDED)

TIMEOUT = aiohttp.ClientTimeout(total=20)
# Срок ссылки, если Platega его не прислала: так обещано в документации.
DEFAULT_TTL = timedelta(minutes=15)
_TTL = re.compile(r"^(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})$")


class PlategaError(Exception):
    """Platega не ответила или ответила ошибкой — текст для журнала, не для покупателя."""


@dataclass(frozen=True)
class Link:
    transaction_id: str
    url: str
    status: str
    expires_at: datetime


@dataclass(frozen=True)
class Status:
    transaction_id: str
    status: str
    amount: float
    currency: str


def expires_at(raw: str | None, now: datetime | None = None) -> datetime:
    """«00:15:00» (или «1.00:00:00») от момента создания → момент окончания, UTC."""
    now = now or datetime.now(timezone.utc)
    match = _TTL.match((raw or "").strip())
    if not match:
        return now + DEFAULT_TTL
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return now + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class Platega:
    def __init__(self, merchant_id: str, secret: str, base_url: str = "https://app.platega.io") -> None:
        self.merchant_id = merchant_id
        self.secret = secret
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def is_genuine(self, merchant_id: str | None, secret: str | None) -> bool:
        """Уведомление пришло с нашими MerchantId и ключом — сравнение без утечки по времени."""
        return hmac.compare_digest(merchant_id or "", self.merchant_id) and hmac.compare_digest(
            secret or "", self.secret
        )

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base_url + path
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.request(method, url, json=payload, headers=self._headers()) as res:
                    text = await res.text()
                    if res.status >= 400:
                        raise PlategaError("%s %s → HTTP %d: %s" % (method, path, res.status, text[:300]))
                    try:
                        data = await res.json(content_type=None)
                    except ValueError:
                        raise PlategaError("%s %s → не JSON: %s" % (method, path, text[:300]))
        except aiohttp.ClientError as exc:
            raise PlategaError("%s %s → нет связи: %s" % (method, path, exc))
        except TimeoutError:
            raise PlategaError("%s %s → нет ответа за %d с" % (method, path, TIMEOUT.total))
        if not isinstance(data, dict):
            raise PlategaError("%s %s → неожиданный ответ: %s" % (method, path, text[:300]))
        return data

    async def create(
        self,
        amount: int,
        description: str,
        order_id: str,
        payload: str,
        user_id: int,
        user_name: str,
        return_url: str,
        failed_url: str,
    ) -> Link:
        """Транзакция без заданного способа: покупатель выбирает его на странице Platega.

        metadata.userId — Telegram ID покупателя: по нему антифрод Platega
        связывает платежи одного человека. Для части магазинов он обязателен.
        """
        data = await self._call("POST", "/v2/transaction/process", {
            "paymentDetails": {"amount": amount, "currency": "RUB"},
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": payload,
            "orderId": order_id,
            "metadata": {"userId": str(user_id), "userName": user_name},
        })
        transaction_id = str(data.get("transactionId") or data.get("id") or "")
        url = str(data.get("url") or data.get("redirect") or "")
        if not transaction_id or not url.startswith("https://"):
            raise PlategaError("создание платежа → нет номера или ссылки: %s" % str(data)[:300])
        return Link(transaction_id, url, str(data.get("status") or PENDING), expires_at(data.get("expiresIn")))

    async def status(self, transaction_id: str) -> Status:
        data = await self._call("GET", "/transaction/%s" % transaction_id)
        details = data.get("paymentDetails") or {}
        if not isinstance(details, dict):
            details = {}
        try:
            amount = float(details.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        return Status(
            transaction_id=str(data.get("id") or transaction_id),
            status=str(data.get("status") or PENDING).upper(),
            amount=amount,
            currency=str(details.get("currency") or "RUB"),
        )
