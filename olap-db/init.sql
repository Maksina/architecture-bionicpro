CREATE TABLE IF NOT EXISTS emg_sensor_data (
    user_id UInt32,
    prosthesis_type String,
    muscle_group String,
    signal_frequency UInt32,
    signal_duration UInt32,
    signal_amplitude Decimal(5,2),
    signal_time DateTime
) ENGINE = MergeTree()
ORDER BY (user_id, prosthesis_type, signal_time);

CREATE TABLE IF NOT EXISTS user_usage_report (
    user_id UInt32,
    customer_name String,
    customer_email String,
    customer_country String,
    prosthesis_type String,
    muscle_group String,
    signal_frequency UInt32,
    signal_duration UInt32,
    signal_amplitude Decimal(5,2),
    signal_time DateTime
) ENGINE = MergeTree()
ORDER BY (user_id, signal_time);

CREATE TABLE IF NOT EXISTS raw_customers (
    id UInt32,
    name String,
    email String,
    country String,
    __op String,
    ts DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(ts)
ORDER BY id;

CREATE TABLE IF NOT EXISTS kafka_customers (
    id UInt32,
    name String,
    email String,
    country String,
    __op String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'crm_server.public.customers',
    kafka_group_name = 'clickhouse_consumer',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 1,
    kafka_skip_broken_messages = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_kafka_to_raw
TO raw_customers AS
SELECT
    id,
    name,
    email,
    country,
    __op,
    now() AS ts
FROM kafka_customers;

CREATE DICTIONARY IF NOT EXISTS customers_dict
(
    id UInt32,
    name String,
    email String,
    country String
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(
    host 'localhost'
    port 9000
    user 'default'
    table 'raw_customers'
))
LIFETIME(MIN 5 MAX 10)
LAYOUT(HASHED());

CREATE TABLE IF NOT EXISTS user_usage_report_new (
    user_id UInt32,
    customer_name String,
    customer_email String,
    customer_country String,
    prosthesis_type String,
    muscle_group String,
    signal_frequency UInt32,
    signal_duration UInt32,
    signal_amplitude Decimal(5,2),
    signal_time DateTime
) ENGINE = MergeTree()
ORDER BY (user_id, signal_time);


CREATE MATERIALIZED VIEW IF NOT EXISTS mv_user_usage_report_new
TO user_usage_report_new AS
SELECT
    e.user_id,
    dictGetString('customers_dict', 'name', e.user_id) AS customer_name,
    dictGetString('customers_dict', 'email', e.user_id) AS customer_email,
    dictGetString('customers_dict', 'country', e.user_id) AS customer_country,
    e.prosthesis_type,
    e.muscle_group,
    e.signal_frequency,
    e.signal_duration,
    e.signal_amplitude,
    e.signal_time
FROM emg_sensor_data e;


-- Загрузка начальных данных
INSERT INTO emg_sensor_data
SELECT *
FROM file('olap.csv', 'CSV');