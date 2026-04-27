# tasktrack

任務追蹤小服務，專為 [learn_cicd 課程](../README.md) 當示範用。

## 技術棧

- Python 3.12
- FastAPI
- SQLAlchemy 2.0 + SQLite（Ch 4 起會升級到 PostgreSQL）
- pytest

## 本地開發

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload
```

## 測試

```bash
pytest -v
```

## API

| Method | Path | 說明 |
|---|---|---|
| POST | `/tasks` | 建任務。Body: `{"title": "...", "milestone": "..."}`（milestone 可選） |
| GET | `/tasks` | 列所有任務 |
| PATCH | `/tasks/{id}/complete` | 標記任務完成 |

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./tasktrack.db` | DB 連線字串，Ch 4 會改成 Postgres |
