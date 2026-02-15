from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.hooks.postgres_hook import PostgresHook
from clickhouse_driver import Client
import pandas as pd

# --- Конфигурация DAG ---
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2026, 2, 15),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'etl_crm_emg_data',
    default_args=default_args,
    description='ETL pipeline for CRM and EMG sensor data',
    schedule_interval=timedelta(hours=1),  # Запуск каждые 1 час
    catchup=False,
    tags=['etl', 'crm', 'emg', 'olap'],
)

# --- Задачи DAG ---

def extract_crm_data(**context):
    """
    Извлекаем данные из CRM (PostgreSQL)
    """
    print("Extracting CRM data...")
    pg_hook = PostgresHook(postgres_conn_id='crm_postgres')  # см. настройку подключения в Airflow
    sql = "SELECT id, name, email, country FROM customers;"
    records = pg_hook.get_records(sql)
    
    # Преобразуем в DataFrame
    df = pd.DataFrame(records, columns=['id', 'name', 'email', 'country'])
    print(f"Extracted {len(df)} records from CRM.")
    
    # Сохраняем в XCom
    context['ti'].xcom_push(key='crm_data', value=df.to_json())


def extract_emg_data(**context):
    """
    Извлекаем данные из OLAP (ClickHouse)
    """
    print("Extracting EMG data...")
    client = Client(
        host='olap_db',  # имя сервиса в docker-compose
        port=9000,
        user='default',  # или ваш пользователь
        password='',     # или ваш пароль
        database='default'  # или ваша БД
    )
    query = "SELECT * FROM emg_sensor_data;"
    rows = client.execute(query)
    
    # Преобразуем в DataFrame
    df = pd.DataFrame(rows, columns=['user_id', 'prosthesis_type', 'muscle_group', 'signal_frequency', 'signal_duration', 'signal_amplitude', 'signal_time'])
    print(f"Extracted {len(df)} records from EMG sensor data.")
    
    # Сохраняем в XCom
    context['ti'].xcom_push(key='emg_data', value=df.to_json())


def transform_and_load(**context):
    print("Transforming and loading data...")
    ti = context['ti']
    crm_json = ti.xcom_pull(task_ids='extract_crm_data', key='crm_data')
    emg_json = ti.xcom_pull(task_ids='extract_emg_data', key='emg_data')

    df_crm = pd.read_json(crm_json)
    df_emg = pd.read_json(emg_json)

    df_crm.rename(columns={'id': 'user_id'}, inplace=True)
    merged_df = pd.merge(df_emg, df_crm, on='user_id', how='left')

    client = Client(
        host='olap_db',
        port=9000,
        user='default',
        password='',
        database='default'
    )

    # Очистим витрину перед загрузкой (для тестирования)
    client.execute("TRUNCATE TABLE IF EXISTS user_usage_report")

    # Подготовим данные для вставки
    rows = []
    for _, row in merged_df.iterrows():
        rows.append((
            int(row['user_id']),
            str(row['name']) if pd.notna(row['name']) else '',
            str(row['email']) if pd.notna(row['email']) else '',
            str(row['country']) if pd.notna(row['country']) else '',
            str(row['prosthesis_type']),
            str(row['muscle_group']),
            int(row['signal_frequency']),
            int(row['signal_duration']),
            float(row['signal_amplitude']),
            row['signal_time']
        ))

    # Вставляем данные
    client.execute(
        '''
        INSERT INTO user_usage_report (
            user_id, customer_name, customer_email, customer_country,
            prosthesis_type, muscle_group, signal_frequency,
            signal_duration, signal_amplitude, signal_time
        ) VALUES
        ''',
        rows
    )

    print(f"Loaded {len(merged_df)} records into user_usage_report.")


# --- Определение задач ---
extract_crm_task = PythonOperator(
    task_id='extract_crm_data',
    python_callable=extract_crm_data,
    dag=dag,
)

extract_emg_task = PythonOperator(
    task_id='extract_emg_data',
    python_callable=extract_emg_data,
    dag=dag,
)

transform_load_task = PythonOperator(
    task_id='transform_and_load',
    python_callable=transform_and_load,
    dag=dag,
)

# --- Порядок выполнения ---
extract_crm_task >> extract_emg_task >> transform_load_task