# Hello Generator API

A sample FastAPI application demonstrating modern Python API development patterns.

## Features

- FastAPI with async/await support
- Pydantic V2 for request/response validation
- Custom middleware for request logging
- Comprehensive test coverage
- OpenAPI documentation

## Endpoints

- `GET /health` - Health check endpoint
- `GET /version` - Application version
- `POST /echo` - Echo message endpoint
- `POST /items` - Create item endpoint

## Setup

1. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Run

Start the development server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API documentation available at: http://localhost:8000/docs

## Test

Run tests:
```bash
pytest tests/ -v
```

Run tests with coverage:
```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

## Project Structure

```
hello_generator/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app and middleware
│   ├── routes.py        # API endpoints
│   └── schemas.py       # Pydantic models
├── tests/
│   ├── conftest.py
│   ├── test_echo.py
│   ├── test_items.py
│   └── test_routes.py
├── requirements.txt
└── README.md
```

## API Examples

### Echo Message
```bash
curl -X POST http://localhost:8000/echo \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, World!"}'
```

### Create Item
```bash
curl -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Widget", "description": "A useful widget", "price": 19.99}'
```

### Health Check
```bash
curl http://localhost:8000/health
```

## License

Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
