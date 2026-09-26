"""Инструкция по установке: один шаблон для витрины и для чата бота.

Продавец не пишет инструкцию каждый раз заново — он отправляет этот шаблон
и при желании добавляет примечание к конкретному заказу. Слайды шагов лежат
в webapp/img/instruction/ и правятся без пересборки образа.
"""
from __future__ import annotations

REGER_BOT = "iRegerBot"
SIGNER_BOT = "isignerbot"

# Боты, на которые ведут ссылки в начале инструкции.
BOTS = (
    {"username": REGER_BOT, "title": "iReg", "hint": "регистрация UDID и сертификат"},
    {"username": SIGNER_BOT, "title": "iSign", "hint": "подпись и установка приложения"},
)

#   title    — что делает покупатель на этом шаге
#   subtitle — как именно
#   hint     — подсказка под картинкой, та же, что на слайде
STEPS = (
    {
        "title": "Введите свой UDID",
        "subtitle": "Откройте @%s и отправьте UDID" % REGER_BOT,
        "hint": "Используйте UDID из вашего заказа",
    },
    {
        "title": "Получите сертификат",
        "subtitle": "Нажмите «Получить сертификат»",
        "hint": "Сохраните .p12, .mobileprovision и пароль",
    },
    {
        "title": "Добавьте сертификат",
        "subtitle": "@%s → «Мои сертификаты» → «Добавить»" % SIGNER_BOT,
        "hint": "Дальше бот попросит файлы по очереди",
    },
    {
        "title": "Отправьте файл .p12",
        "subtitle": "Перешлите файл из @%s в @%s" % (REGER_BOT, SIGNER_BOT),
        "hint": "Выберите файл с расширением .p12",
    },
    {
        "title": "Введите пароль",
        "subtitle": "Возьмите пароль из сообщения с сертификатом",
        "hint": "В кадре — пример, ваш пароль может отличаться",
    },
    {
        "title": "Отправьте .mobileprovision",
        "subtitle": "Перешлите второй файл из @%s" % REGER_BOT,
        "hint": "Дождитесь сообщения «Сертификат добавлен»",
    },
    {
        "title": "Нажмите «Подписать iPA»",
        "subtitle": "В меню @%s выберите подпись приложения" % SIGNER_BOT,
        "hint": "Если появится второе меню — снова «Подписать iPA»",
    },
    {
        "title": "Отправьте приложение",
        "subtitle": "Отправьте нужный файл в формате .ipa",
        "hint": "Дождитесь завершения загрузки файла",
    },
    {
        "title": "Выберите свой сертификат",
        "subtitle": "Если бот предложит выбрать сертификат",
        "hint": "Сверьте название с сообщением @%s" % REGER_BOT,
    },
    {
        "title": "Установите приложение",
        "subtitle": "После подписи нажмите «Установить»",
        "hint": "Далее следуйте подсказкам установки на iPhone",
    },
)


def image(number: int) -> str:
    """Адрес слайда шага в статике Mini App."""
    return "/static/img/instruction/step-%02d.webp" % number


def public_steps() -> list[dict]:
    """Шаги для витрины: к тексту добавлен номер и картинка."""
    return [
        dict(step, number=i, image=image(i))
        for i, step in enumerate(STEPS, start=1)
    ]


def bot_links() -> list[dict]:
    return [dict(bot, url="https://t.me/%s" % bot["username"]) for bot in BOTS]
