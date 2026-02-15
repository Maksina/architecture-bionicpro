import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from clickhouse_driver import Client
import pandas as pd
from io import BytesIO
import json
from decimal import Decimal
from minio import Minio
from minio.error import S3Error
import hashlib
from datetime import datetime

app = FastAPI()

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "olap_db")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", 9000))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "default")

CDN_BASE_URL = os.getenv("CDN_BASE_URL", "http://localhost:8083")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio_user")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio_password")
MINIO_SECURE = os.getenv("MINIO_SECURE", "False").lower() == "true"
REPORTS_BUCKET = os.getenv("REPORTS_BUCKET", "reports")

def get_clickhouse_client():
    return Client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        user=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE
    )

def get_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )

def convert_decimal_to_float(obj):
    if isinstance(obj, dict):
        return {key: convert_decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal_to_float(item) for item in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

@app.get("/reports")
async def get_reports(request: Request):
    email = request.headers.get("X-User-Email")
    if not email:
        raise HTTPException(status_code=400, detail="X-User-Email header is required")

    # Генерируем имя файла на основе email
    report_filename = f"{hashlib.sha256(email.encode()).hexdigest()}.json"

    minio_client = get_minio_client()

    # Проверяем, есть ли отчёт в MinIO
    try:
        stat = minio_client.stat_object(REPORTS_BUCKET, report_filename)
        # Если файл есть, возвращаем ссылку на CDN (Nginx)
        cdn_url = f"{CDN_BASE_URL}/reports/{report_filename}"
        return JSONResponse({"cdn_url": cdn_url, "cached_at": stat.last_modified.isoformat()})
    except S3Error:
        # Файла нет, генерируем отчёт
        pass

    # Получаем данные из ClickHouse
    client = get_clickhouse_client()
    query = "SELECT * FROM user_usage_report WHERE customer_email = %(email)s ORDER BY signal_time DESC LIMIT 100"
    rows = client.execute(query, {'email': email})

    columns = [
        'user_id', 'customer_name', 'customer_email', 'customer_country',
        'prosthesis_type', 'muscle_group', 'signal_frequency',
        'signal_duration', 'signal_amplitude', 'signal_time'
    ]
    result = [dict(zip(columns, row)) for row in rows]
    result = convert_decimal_to_float(result)

    # Сохраняем в MinIO
    buffer = BytesIO()
    buffer.write(json.dumps(result, default=str).encode())
    buffer.seek(0)

    try:
        minio_client.put_object(
            REPORTS_BUCKET,
            report_filename,
            buffer,
            length=len(buffer.getvalue()),
            content_type="application/json"
        )
    except S3Error as e:
        raise HTTPException(status_code=500, detail=f"Failed to save report to MinIO: {e}")

    # Возвращаем CDN URL
    cdn_url = f"http://nginx:8080/reports/{report_filename}"
    return JSONResponse({"cdn_url": cdn_url, "cached_at": datetime.utcnow().isoformat()})

@app.get("/reports/raw")
async def get_reports_raw(request: Request):
    # Тот же эндпоинт, но без кеширования
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
    result = [dict(zip(columns, row)) for row in rows]
    result = convert_decimal_to_float(result)

    return JSONResponse(result)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)