# Source từ project root trong cả Terminal chạy server và Terminal chạy CLI
if [ ! -f scripts/week7_env.sh ]; then
    echo 'Hãy cd vào project root trước khi source scripts/week7_env.sh' >&2
    return 1
fi

export PATH="$PWD/data/tmp/week7/airflow-venv/bin:$PATH"
export AIRFLOW_HOME="$PWD/data/tmp/week7/airflow-home"
export AIRFLOW__CORE__DAGS_FOLDER="$PWD/airflow/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=1
export AIRFLOW__DAG_PROCESSOR__PARSING_PROCESSES=1
export AIRFLOW__API__HOST=127.0.0.1
export AIRFLOW__API__PORT=8082
# Nếu không đặt base URL, task sẽ gọi mặc định cổng 8080 và bị lỗi token
export AIRFLOW__API__BASE_URL=http://127.0.0.1:8082
