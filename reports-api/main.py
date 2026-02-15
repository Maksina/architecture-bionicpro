import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from clickhouse_driver import Client
import pandas as pd
from io import BytesIO
import json
from decimal import Decimal

app = FastAPI()

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "olap_db")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", 9000))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "default")

def get_clickhouse_client():
    return Client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        user=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE
    )

def convert_decimal_to_float(obj):
    """
    Рекурсивно конвертирует Decimal в float в объекте.
    """
    if isinstance(obj, dict):
        return {key: convert_decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal_to_float(item) for item in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

@app.get("/reports")
async def get_reports_pdf(request: Request):
    email = request.headers.get("X-User-Email")
    if not email:
        raise HTTPException(status_code=400, detail="X-User-Email header is required")

    client = get_clickhouse_client()

    query = "SELECT * FROM user_usage_report WHERE customer_email = %(email)s ORDER BY signal_time DESC LIMIT 100"
    rows = client.execute(query, {'email': email})

    columns = [
        'user_id', 'customer_name', 'customer_email', 'customer_country',
        'prosthesis_type', 'muscle_group', 'signal_frequency',
        'signal_duration', 'signal_amplitude', 'signal_time'
    ]
    df = pd.DataFrame(rows, columns=columns)

    # Преобразуем Decimal в float в DataFrame
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: float(x) if isinstance(x, Decimal) else x)

    # Сохраняем в CSV в памяти
    buffer = BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=report.csv"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)