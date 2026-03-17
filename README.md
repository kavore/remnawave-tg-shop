# Telegram-бот для продажи подписок Remnawave

Этот Telegram-бот предназначен для автоматизации продажи и управления подписками для панели **Remnawave**. Он интегрируется с API Remnawave для управления пользователями и подписками, а также использует различные платежные системы для приема платежей.

## ✨ Ключевые возможности

### Для пользователей:
-   **Регистрация и выбор языка:** Поддержка русского и английского языков.
-   **Просмотр подписки:** Пользователи могут видеть статус своей подписки, дату окончания и ссылку на конфигурацию.
-   **Мои устройства:** Опциональный раздел для просмотра и отключения подключенных устройств (активируется через переменную `MY_DEVICES_SECTION_ENABLED`).
-   **Пробная подписка:** Система пробных подписок для новых пользователей (активируется вручную по кнопке).
-   **Промокоды:** Возможность применять промокоды для получения скидок или бонусных дней.
-   **Реферальная программа:** Пользователи могут приглашать друзей и получать за это бонусные дни подписки.
    -   **Оплата:** Поддержка оплаты через YooKassa, FreeKassa (REST API), Platega, SeverPay, CryptoPay и Telegram Stars.

### Для администраторов:
-   **Защищенная админ-панель:** Доступ только для администраторов, указанных в `ADMIN_IDS`.
-   **Статистика:** Просмотр статистики использования бота (общее количество пользователей, забаненные, активные подписки), недавние платежи и статус синхронизации с панелью.
-   **Управление пользователями:** Блокировка/разблокировка пользователей, просмотр списка забаненных и детальной информации о пользователе.
-   **Рассылка:** Отправка сообщений всем пользователям, пользователям с активной или истекшей подпиской.
-   **Управление промокодами:** Создание и просмотр промокодов.
-   **Синхронизация с панелью:** Ручной запуск синхронизации пользователей и подписок с панелью Remnawave.
-   **Логи действий:** Просмотр логов всех действий пользователей.

## 🚀 Технологии

-   **Python 3.12**
-   **Aiogram 3.x:** Асинхронный фреймворк для Telegram ботов.
-   **aiohttp:** Для запуска веб-сервера (вебхуки).
-   **SQLAlchemy 2.x & asyncpg:** Асинхронная работа с базой данных PostgreSQL.
-   **Alembic:** Миграции схемы базы данных.
-   **YooKassa, FreeKassa API, Platega, SeverPay, aiocryptopay:** Интеграции с платежными системами.
-   **Pydantic:** Для управления настройками из `.env` файла.
-   **Docker & Docker Compose:** Для контейнеризации и развертывания.

## ⚙️ Установка и запуск

### Предварительные требования

-   Установленные Docker и Docker Compose.
-   Рабочая панель Remnawave.
-   Токен Telegram-бота.
-   Данные для подключения к платежным системам (YooKassa, CryptoPay и т.д.).

### Шаги установки

1.  **Клонируйте репозиторий:**
    ```bash
    git clone https://github.com/kavore/remnawave-tg-shop
    cd remnawave-tg-shop
    ```

2.  **Создайте и настройте файл `.env`:**
    Скопируйте `env.example` в `.env` и заполните своими данными.
    ```bash
    cp .env.example .env
    nano .env 
    ```
    Ниже перечислены ключевые переменные.

    <details>
    <summary><b>Основные настройки</b></summary>

    | Переменная | Описание | Пример |
    | --- | --- | --- |
    | `BOT_TOKEN` | **Обязательно.** Токен вашего Telegram-бота. | `1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
    | `ADMIN_IDS` | **Обязательно.** ID администраторов в Telegram через запятую. | `12345678,98765432` |
    | `DEFAULT_LANGUAGE` | Язык по умолчанию для новых пользователей. | `ru` |
    | `SUPPORT_LINK` | (Опционально) Ссылка на поддержку. | `https://t.me/your_support` |
    | `SUBSCRIPTION_MINI_APP_URL` | (Опционально) URL Mini App для показа подписки. | `https://t.me/your_bot/app` |
    | `MY_DEVICES_SECTION_ENABLED` | Включить раздел «Мои устройства» в меню подписки (`true`/`false`). | `false` |
    | `REQUIRED_CHANNEL_SUBSCRIBE_TO_USE` | Включить/выключить обязательную проверку подписки на канал (`true`/`false`). | `false` |
    | `REQUIRED_CHANNEL_ID` | ID канала для проверки подписки. Используется, только если `REQUIRED_CHANNEL_SUBSCRIBE_TO_USE=true`. | `-1001234567890` |
    | `REQUIRED_CHANNEL_LINK` | (Опционально) Публичная ссылка или invite на канал для кнопки «Проверить подписку». | `https://t.me/your_channel` |
    | `REFERRAL_ENABLED` | Включить/выключить реферальную систему полностью (`true`/`false`). | `true` |
    </details>

    <details>
    <summary><b>Настройки платежей и вебхуков</b></summary>

    | Переменная | Описание |
    | --- | --- |
    | `WEBHOOK_BASE_URL`| **Обязательно.** Базовый URL для вебхуков, например `https://your.domain.com`. |
    | `TELEGRAM_WEBHOOK_PATH` | Относительный путь Telegram вебхука. По умолчанию `/webhook/telegram`. |
    | `TELEGRAM_WEBHOOK_SECRET` | (Рекомендуется) Секрет для проверки заголовка `X-Telegram-Bot-Api-Secret-Token`. |
    | `WEB_SERVER_HOST` | Хост для веб-сервера. По умолчанию `0.0.0.0`. | `0.0.0.0` |
    | `WEB_SERVER_PORT` | Порт для веб-сервера. | `8080` |
    | `PAYMENT_METHODS_ORDER` | (Опционально) Порядок отображения кнопок оплаты через запятую. Поддерживаемые ключи: `severpay`, `freekassa`, `platega`, `yookassa`, `stars`, `cryptopay`. Первый будет сверху. |
    | `YOOKASSA_ENABLED` | Включить/выключить YooKassa (`true`/`false`). |
    | `YOOKASSA_SHOP_ID` | ID вашего магазина в YooKassa. |
    | `YOOKASSA_SECRET_KEY`| Секретный ключ магазина YooKassa. |
    | `YOOKASSA_TAX_SYSTEM_CODE` | (Опционально) Код СНО для чеков YooKassa (`1-6` по 54-ФЗ: ОСН, УСН доход, УСН доход-расход, ЕНВД, ЕСХН, ПСН). Передавайте только если ваша онлайн-касса требует `tax_system_code`; иначе параметр будет пропущен. |
    | `YOOKASSA_PAYMENT_MODE` | (Опционально) Переопределяет `receipt.items[].payment_mode` для YooKassa. Если не задано, используется `full_prepayment` для обычных платежей и `full_payment` при `YOOKASSA_AUTOPAYMENTS_ENABLED=true`. |
    | `YOOKASSA_PAYMENT_SUBJECT` | (Опционально) Переопределяет `receipt.items[].payment_subject` для YooKassa. Если не задано, используется `payment` для обычных платежей и `service` при `YOOKASSA_AUTOPAYMENTS_ENABLED=true`. |
    | `YOOKASSA_AUTOPAYMENTS_ENABLED` | Включить автопродление (сохранение карт, автосписания, управление способами оплаты). |
    | `YOOKASSA_AUTOPAYMENTS_REQUIRE_CARD_BINDING` | Требовать обязательную привязку карты при оплате с автосписанием. Установите `false`, чтобы пользователю показывался чекбокс «Сохранить карту». |
    | `NALOGO_INN` | ИНН для авторизации в nalog.ru (самозанятый). |
    | `NALOGO_PASSWORD` | Пароль для авторизации в nalog.ru (самозанятый). |
    | `CRYPTOPAY_ENABLED` | Включить/выключить CryptoPay (`true`/`false`). |
    | `CRYPTOPAY_TOKEN` | Токен из вашего CryptoPay App. |
    | `FREEKASSA_ENABLED` | Включить/выключить FreeKassa (`true`/`false`). |
    | `FREEKASSA_MERCHANT_ID` | ID вашего магазина в FreeKassa. |
    | `FREEKASSA_API_KEY` | API-ключ для запросов к FreeKassa REST API. |
    | `FREEKASSA_SECOND_SECRET` | Секретное слово №2 — используется для проверки уведомлений от FreeKassa. |
    | `FREEKASSA_PAYMENT_URL` | (Опционально, legacy SCI) Базовый URL платёжной формы FreeKassa. По умолчанию `https://pay.freekassa.ru/`. |
    | `FREEKASSA_PAYMENT_IP` | Внешний IP вашего сервера, который будет передаваться в запрос оплаты. |
    | `FREEKASSA_PAYMENT_METHOD_ID` | ID метода оплаты через магазин FreeKassa. По умолчанию `44`. |
    | `STARS_ENABLED` | Включить/выключить Telegram Stars (`true`/`false`). |
    | `STARS_PROVIDER_TOKEN` | Токен провайдера Telegram invoice. Для Stars (XTR) оставить пустым. |
    | `PLATEGA_ENABLED` | Включить/выключить Platega (`true`/`false`). |
    | `PLATEGA_MERCHANT_ID` | MerchantId из личного кабинета Platega. |
    | `PLATEGA_SECRET` | API секрет для запросов Platega. |
    | `PLATEGA_PAYMENT_METHOD` | ID способа оплаты (2 — SBP QR, 10 — РФ карты, 12 — международные карты, 13 — crypto). |
    | `PLATEGA_RETURN_URL` | (Опционально) URL редиректа после успешной оплаты. По умолчанию ссылка на бота. |
    | `PLATEGA_FAILED_URL` | (Опционально) URL редиректа при ошибке/отмене. По умолчанию как `PLATEGA_RETURN_URL`. |
    | `SEVERPAY_ENABLED` | Включить/выключить SeverPay (`true`/`false`). |
    | `SEVERPAY_MID` | MID магазина в SeverPay. |
    | `SEVERPAY_TOKEN` | Секрет/токен для подписи запросов SeverPay. |
    | `SEVERPAY_BASE_URL` | (Опционально) Базовый URL API SeverPay. По умолчанию `https://severpay.io/api/merchant`. |
    | `SEVERPAY_RETURN_URL` | (Опционально) URL редиректа после оплаты (по умолчанию ссылка на бота). |
    | `SEVERPAY_LIFETIME_MINUTES` | (Опционально) Время жизни платежной ссылки в минутах (30–4320). |
    </details>

    <details>
    <summary><b>Настройки логирования</b></summary>

    | Переменная | Описание | Пример |
    | --- | --- | --- |
    | `LOGS_PAGE_SIZE` | Количество записей на странице в разделе админ-логов. | `10` |
    | `LOG_STORE_MESSAGE_CONTENT` | Сохранять ли содержимое сообщений/колбэков в БД логов (`true`/`false`). | `false` |
    | `LOG_STORE_RAW_UPDATES` | Сохранять ли превью сырого Telegram update в БД логов (`true`/`false`). | `false` |
    | `LOG_EXPORT_INCLUDE_SENSITIVE` | Добавлять ли в CSV экспорт чувствительные поля (`content`, `raw_update_preview`). | `false` |
    | `LOG_ADMIN_HIDE` | Скрывать админские события (`ADMIN_IDS`) в интерфейсе «Все логи сообщений» и в CSV экспорте (`true`/`false`). Логи продолжают записываться в БД. | `true` |
    </details>

    <details>
    <summary><b>Настройки подписок</b></summary>

    Для каждого периода (1, 3, 6, 12 месяцев) можно настроить доступность и цены:
    - `1_MONTH_ENABLED`: `true` или `false`
    - `RUB_PRICE_1_MONTH`: Цена в рублях
    - `STARS_PRICE_1_MONTH`: Цена в Telegram Stars
    Аналогичные переменные есть для `3_MONTHS`, `6_MONTHS`, `12_MONTHS`.
    </details>

    <details>
    <summary><b>Настройки панели Remnawave</b></summary>
    
    | Переменная | Описание |
    | --- | --- |
    | `PANEL_API_URL` | URL API вашей панели Remnawave. |
    | `PANEL_API_KEY` | API ключ для доступа к панели. |
    | `PANEL_WEBHOOK_SECRET`| Секретный ключ для проверки вебхуков от панели. |
    | `USER_SQUAD_UUIDS` | ID отрядов для новых пользователей. |
    | `USER_EXTERNAL_SQUAD_UUID` | Опционально. UUID внешнего отряда (External Squad) из [документации Remnawave](https://docs.rw/api), куда автоматически добавляются новые пользователи. |
    | `USER_TRAFFIC_LIMIT_GB`| Лимит трафика в ГБ (0 - безлимит). |
    | `USER_HWID_DEVICE_LIMIT`| Лимит устройств (HWID) для новых пользователей (0 - безлимит). |

    > Раздел "Мои устройства" становится доступен пользователям только при включении `MY_DEVICES_SECTION_ENABLED`. Значение лимита устройств при создании записей в панели берётся из `USER_HWID_DEVICE_LIMIT`.
    </details>

    <details>
    <summary><b>Настройки пробного периода</b></summary>

    | Переменная | Описание |
    | --- | --- |
    | `TRIAL_ENABLED` | Включить/выключить пробный период (`true`/`false`). |
    | `TRIAL_DURATION_DAYS`| Длительность пробного периода в днях. |
    | `TRIAL_TRAFFIC_LIMIT_GB`| Лимит трафика для пробного периода в ГБ. |
    </details>

3.  **Запустите контейнеры:**
    ```bash
    docker compose up -d
    ```
    Эта команда скачает образ и запустит сервис в фоновом режиме.

4.  **Настройка вебхуков (Обязательно):**
    Вебхуки являются **обязательным** компонентом для работы бота, так как они используются для получения уведомлений от платежных систем (YooKassa, FreeKassa, CryptoPay, Platega, SeverPay) и панели Remnawave.

    Вам понадобится обратный прокси (например, Nginx) для обработки HTTPS-трафика и перенаправления запросов на контейнер с ботом.

    **Пути для перенаправления:**
    -   `https://<ваш_домен>/webhook/yookassa` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/yookassa`
    -   `https://<ваш_домен>/webhook/freekassa` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/freekassa`
    -   `https://<ваш_домен>/webhook/platega` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/platega`
    -   `https://<ваш_домен>/webhook/severpay` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/severpay`
    -   `https://<ваш_домен>/webhook/cryptopay` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/cryptopay`
    -   `https://<ваш_домен>/webhook/panel` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/panel`
    -   **Для Telegram:** Бот автоматически установит вебхук, если в `.env` указан `WEBHOOK_BASE_URL`. Путь берётся из `TELEGRAM_WEBHOOK_PATH` (по умолчанию `https://<ваш_домен>/webhook/telegram`).

    Где `remnawave-tg-shop` — это имя сервиса из `docker-compose.yml`, а `<WEB_SERVER_PORT>` — порт, указанный в `.env`.

5.  **Просмотр логов:**
    ```bash
    docker compose logs -f remnawave-tg-shop
    ```

    > 💡 Если включена проверка подписки (`REQUIRED_CHANNEL_SUBSCRIBE_TO_USE=true`), добавьте бота администратором в канал из `REQUIRED_CHANNEL_ID`. Пользователь увидит кнопку «Проверить подписку», и после успешного подтверждения доступ продолжится.

### Миграции БД (Alembic)

- При запуске `python main.py` миграции применяются автоматически до `head`.
- Для ручного запуска используйте:

```bash
alembic upgrade head
```

## Подробная инструкция для развертывания на сервере с панелью Remnawave

### 1. Клонирование репозитория

```bash
git clone https://github.com/kavore/remnawave-tg-shop && cd remnawave-tg-shop
```

### 2. Настройка переменных окружения

```bash
cp .env.example .env && nano .env
```

**Обязательные поля для заполнения:**
- `BOT_TOKEN` - токен телеграмм бота, например, `234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
- `ADMIN_IDS` - TG ID администраторов, например, `12345678,98765432` и т.д. (через запятую без пробелов)
- `WEBHOOK_BASE_URL` - Обязательно. Базовый URL для вебхуков, например `https://webhook.domain.com`
- `PANEL_API_URL` - URL API вашей панели Remnawave (например, `http://remnawave:3000/api` или `https://panel.domain.com/api`)
- `PANEL_API_KEY` - API ключ для доступа к панели (генерируется из UI-интерфейса панели)
- `PANEL_WEBHOOK_SECRET` - Секретный ключ для проверки вебхуков от панели (берётся из `.env` самой панели)
- `USER_SQUAD_UUIDS` - ID отрядов для новых пользователей

### 3. Настройка Reverse Proxy (Nginx)

Перейдите в директорию конфигурации Nginx панели Remnawave:

```bash
cd /opt/remnawave/nginx && nano nginx.conf
```

Добавьте в `nginx.conf` следующую конфигурацию:

```nginx
upstream remnawave-tg-shop {
    server remnawave-tg-shop:8080;
}

map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}

server {
    server_name webhook.domain.com; # Домен для отправки Webhook'ов
    listen 443 ssl;
    http2 on;

    ssl_certificate "/etc/nginx/ssl/webhook_fullchain.pem";
    ssl_certificate_key "/etc/nginx/ssl/webhook_privkey.key";
    ssl_trusted_certificate "/etc/nginx/ssl/webhook_fullchain.pem";

    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
    proxy_intercept_errors on;
    error_page 400 404 500 502 @redirect;

    location / {
        proxy_pass http://remnawave-tg-shop$request_uri;
    }

    location @redirect {
        return 404;
    }
}
```

### 4. Выпуск SSL-сертификата для домена webhook

Убедитесь, что установлены необходимые компоненты, а также откройте 80 порт:

```bash
sudo apt-get install cron socat
curl https://get.acme.sh | sh -s email=EMAIL && source ~/.bashrc
ufw allow 80/tcp && ufw reload
```

Выпустите сертификат:

```bash
acme.sh --set-default-ca --server letsencrypt
acme.sh --issue --standalone -d 'webhook.domain.com' \
  --key-file /opt/remnawave/nginx/webhook_privkey.key \
  --fullchain-file /opt/remnawave/nginx/webhook_fullchain.pem
```

### 5. Добавление сертификатов в Docker Compose Nginx

Отредактируйте `docker-compose.yml` панели Nginx:

```bash
cd /opt/remnawave/nginx && nano docker-compose.yml
```

Добавьте две строки в секцию `volumes`:

```yaml
services:
    remnawave-nginx:
        image: nginx:1.26
        container_name: remnawave-nginx
        hostname: remnawave-nginx
        volumes:
            - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
            - ./fullchain.pem:/etc/nginx/ssl/fullchain.pem:ro
            - ./privkey.key:/etc/nginx/ssl/privkey.key:ro
            - ./subdomain_fullchain.pem:/etc/nginx/ssl/subdomain_fullchain.pem:ro
            - ./subdomain_privkey.key:/etc/nginx/ssl/subdomain_privkey.key:ro
            - ./webhook_fullchain.pem:/etc/nginx/ssl/webhook_fullchain.pem:ro     # Добавьте эту строку
            - ./webhook_privkey.key:/etc/nginx/ssl/webhook_privkey.key:ro         # Добавьте эту строку
        restart: always
        ports:
            - '0.0.0.0:443:443'
        networks:
            - remnawave-network

networks:
    remnawave-network:
        name: remnawave-network
        driver: bridge
        external: true
```

### 6. Запуск бота и перезапуск Nginx

Запустите бота:

```bash
cd /root/remnawave-tg-shop && docker compose up -d && docker compose logs -f -t
```

Перезапустите Nginx:

```bash
cd /opt/remnawave/nginx && docker compose down && docker compose up -d && docker compose logs -f -t
```

## 🐳 Docker

Файлы `Dockerfile` и `docker-compose.yml` уже настроены для сборки и запуска проекта. `docker-compose.yml` использует готовый образ с GitHub Container Registry, но вы можете раскомментировать `build: .` для локальной сборки.

Для автоматической публикации образов настроены GitHub Actions (`.github/workflows`). По умолчанию образы пушатся в GitHub Container Registry и Docker Hub. Добавьте в Secrets репозитория значения `DOCKERHUB_USERNAME` и `DOCKERHUB_TOKEN` (персональный access token или пароль для Docker Hub), чтобы загрузка в Docker Hub работала корректно.

## 📁 Структура проекта

```
.
├── bot/
│   ├── filters/          # Пользовательские фильтры Aiogram
│   ├── handlers/         # Обработчики сообщений и колбэков
│   ├── keyboards/        # Клавиатуры
│   ├── middlewares/      # Промежуточные слои (i18n, проверка бана)
│   ├── services/         # Бизнес-логика (платежи, API панели)
│   ├── states/           # Состояния FSM
│   └── main_bot.py       # Основная логика бота
├── config/
│   └── settings.py       # Настройки Pydantic
├── db/
│   ├── dal/              # Слой доступа к данным (DAL)
│   ├── database_setup.py # Настройка БД
│   └── models.py         # Модели SQLAlchemy
├── locales/              # Файлы локализации (ru, en)
├── .env.example          # Пример файла с переменными окружения
├── Dockerfile            # Инструкции для сборки Docker-образа
├── docker-compose.yml    # Файл для оркестрации контейнеров
├── requirements.txt      # Зависимости Python
└── main.py               # Точка входа в приложение
```

## 🔮 Планы на будущее

-   Расширенные типы промокодов (например, скидки в процентах).

## ❤️ Поддержка
- Карты РФ и зарубежные: [Tribute](https://t.me/tribute/app?startapp=dqdg)
- Crypto: `USDT TRC-20 TT3SqBbfU4vYm6SUwUVNZsy278m2xbM4GE`
