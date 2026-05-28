# Logistics Orders (Django)

Минимално responsive web приложение за вътрешна логистика и задачи за доставка/получаване.

## Setup (dev)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

После отворете:
- App: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`

