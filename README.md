# Logistics Orders (Django)

Минимално responsive web приложение за вътрешна логистика и задачи за доставка/получаване.

## Setup (dev)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_roles
python manage.py seed_sample_data
python manage.py runserver
```

По подразбиране dev конфигурацията ползва локален PostgreSQL:

- database: `logisticsdb-test`
- user: `postgres`
- password: `password`
- host: `localhost`
- port: `5432`

Стойностите могат да се променят с `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` и `POSTGRES_PORT`.

`seed_sample_data` зарежда демо потребители, адреси, 3-4 дневни/седмични постоянни задачи и разнородни доставки само за работните дни от последните 30 дни. Дневният обем е различен, с най-много задачи в понеделник и петък, поне 15 задачи на работен ден и до 5-6 отворени изоставащи задачи само от последните 2-3 дни. Паролата на демо потребителите е `password`.

После отворете:
- App: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`

