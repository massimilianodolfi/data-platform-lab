# Data Platform Lab

A local data-platform engineering lab for Apple Silicon, combining GitHub Actions, a self-hosted runner, Terraform, Docker, kind Kubernetes, Apache Spark, PySpark, JupyterLab, and MinIO-compatible S3 object storage.

## Architecture

```text
GitHub repository
       |
       v
GitHub Actions
       |
       v
Self-hosted macOS ARM64 runner
       |
       +-----------------------------+
       |                             |
       v                             v
Terraform                      Spark deployment
       |                             |
       v                             v
kind Kubernetes cluster       Spark container image
       |                        with Hadoop S3A
       +--------------+--------------+
                      |
                      v
                spark namespace
                      |
              +-------+-------+
              |               |
         Spark driver     Spark executors
              |               |
              +-------+-------+
                      |
                    S3A
                      |
                      v
          MinIO-compatible S3 storage
          host.docker.internal:9000

JupyterLab container
       |
       | Spark client mode
       v
Notebook Spark driver
       |
       +-------> Executors on kind Kubernetes
       |
       +-------> MinIO through S3A

Components
Apple Silicon Mac with 48 GB memory
Docker Desktop
kind Kubernetes cluster with one control-plane and two worker nodes
Terraform Kubernetes provider
GitHub Actions self-hosted ARM64 runner
Apache Spark 4.2.0
PySpark 4.2.0
Java 21
Python 3.10 for the Jupyter driver and Spark Python workers
JupyterLab
MinIO-compatible S3 object storage
Hadoop S3A connector
Repository structure
Text
data-platform-lab/
├── .github/
│   └── workflows/
│       ├── deploy-spark-cluster.yml
│       ├── deploy-spark-infra.yml
│       ├── deploy-storage.yml
│       ├── destroy-spark-cluster.yml
│       ├── terraform.yml
│       └── test-runner.yml
├── kubernetes/
│   ├── kind-config.yaml
│   └── spark/
│       └── spark-rbac.yaml
├── spark/
│   ├── Dockerfile
│   └── jobs/
│       └── hello_spark.py
├── storage/
│   └── docker-compose.yml
├── terraform/
│   ├── main.tf
│   ├── outputs.tf
│   ├── providers.tf
│   ├── variables.tf
│   └── versions.tf
└── README.md

Prerequisites
Bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install git gh kubectl kind helm jq python


Docker Desktop must be installed and running.

Configure these GitHub repository secrets:

Text
MINIO_ROOT_USER
MINIO_ROOT_PASSWORD


Do not store MinIO credentials in the repository or in notebooks.

Workflow order

For a complete deployment, run the workflows in this order:

Text
1. Deploy Spark Infrastructure
2. Deploy MinIO Storage
3. Deploy Spark Software


To remove the Kubernetes cluster and its pods, run:

Text
Destroy Spark kind Cluster


MinIO is managed separately from kind so that object-storage data can survive Kubernetes cluster recreation.

Create the Kubernetes cluster

kubernetes/kind-config.yaml:

Yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4

nodes:
  - role: control-plane
  - role: worker
  - role: worker


Create and verify the cluster:

Bash
kind create cluster \
  --name data-platform-lab \
  --config kubernetes/kind-config.yaml

kubectl config current-context
kubectl get nodes


Expected context:

Text
kind-data-platform-lab

Terraform infrastructure

Terraform manages the spark and storage namespaces.

Bash
cd terraform
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply


Do not commit Terraform state:

Gitignore
**/.terraform/*
*.tfstate
*.tfstate.*
*.tfplan
crash.log

Spark image

The Spark image must include the timezone configuration and Hadoop S3A dependencies required to access MinIO through s3a:// paths.

spark/Dockerfile:

Dockerfile
FROM spark:4.2.0-python3

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Amsterdam
ENV JAVA_TOOL_OPTIONS="-Duser.timezone=Europe/Amsterdam"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
        curl \
        maven && \
    ln -snf /usr/share/zoneinfo/Europe/Amsterdam /etc/localtime && \
    echo "Europe/Amsterdam" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# Detect the Hadoop version bundled with Spark and install the matching
# Hadoop AWS/S3A connector and runtime dependencies.
RUN set -eux; \
    HADOOP_JAR="$(find /opt/spark/jars \
      -maxdepth 1 \
      -type f \
      -name 'hadoop-client-api-*.jar' \
      | head -1)"; \
    test -n "${HADOOP_JAR}"; \
    HADOOP_VERSION="$(basename "${HADOOP_JAR}" \
      | sed -E 's/^hadoop-client-api-(.+)\.jar$/\1/')"; \
    echo "Detected Hadoop version: ${HADOOP_VERSION}"; \
    mvn \
      --batch-mode \
      dependency:copy-dependencies \
      -Dartifact="org.apache.hadoop:hadoop-aws:${HADOOP_VERSION}" \
      -DincludeScope=runtime \
      -DoutputDirectory=/tmp/s3a-jars; \
    cp -v /tmp/s3a-jars/*.jar /opt/spark/jars/; \
    test -f "/opt/spark/jars/hadoop-aws-${HADOOP_VERSION}.jar"; \
    rm -rf /tmp/s3a-jars /root/.m2

COPY jobs/ /opt/spark/jobs/

USER 185


Build and load the image into kind:

Bash
docker build \
  --no-cache \
  --platform linux/arm64 \
  -t data-platform-spark:latest \
  ./spark

kind load docker-image \
  data-platform-spark:latest \
  --name data-platform-lab


Verify S3A support:

Bash
docker run --rm \
  data-platform-spark:latest \
  sh -c 'ls -l /opt/spark/jars/hadoop-aws-*.jar'

Spark RBAC

kubernetes/spark/spark-rbac.yaml should grant the Spark service account permission to manage Spark resources:

Yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark
  namespace: spark

---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-role
  namespace: spark
rules:
  - apiGroups: [""]
    resources:
      - pods
      - services
      - configmaps
      - persistentvolumeclaims
    verbs:
      - create
      - get
      - list
      - watch
      - delete
      - deletecollection
      - patch
      - update

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: spark-role-binding
  namespace: spark
subjects:
  - kind: ServiceAccount
    name: spark
    namespace: spark
roleRef:
  kind: Role
  name: spark-role
  apiGroup: rbac.authorization.k8s.io


Apply it:

Bash
kubectl apply -f kubernetes/spark/spark-rbac.yaml

MinIO storage

MinIO runs as a Docker container outside kind. This keeps object storage separate from the disposable Kubernetes compute cluster.

The storage workflow creates these buckets:

Text
raw
processed
checkpoints
spark-events


Endpoints:

Text
Mac S3 API:         http://127.0.0.1:9000
Spark/kind S3 API:  http://host.docker.internal:9000
Mac console:        http://127.0.0.1:9001


The S3 API port must be reachable from kind pods:

Yaml
ports:
  - "9000:9000"
  - "127.0.0.1:9001:9001"


Use a Docker named volume for persistent storage:

Yaml
services:
  minio:
    volumes:
      - minio-data:/data

volumes:
  minio-data:
    name: data-platform-minio-data


Do not use docker compose down -v unless the intention is to delete all stored data.

Verify MinIO:

Bash
curl -f http://127.0.0.1:9000/minio/health/ready

docker volume inspect data-platform-minio-data

docker inspect minio \
  --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}'


Expected mount:

Text
volume data-platform-minio-data /data

JupyterLab image

Create the JupyterLab image with Java, Python 3.10, PySpark 4.2.0, and the same Hadoop S3A dependencies used by Spark executors.

The Jupyter image also needs S3A because Jupyter hosts the Spark driver in client mode. If only the executor image has S3A, writing s3a://... fails in the driver with ClassNotFoundException: org.apache.hadoop.fs.s3a.S3AFileSystem.

Dockerfile:

Dockerfile
FROM quay.io/jupyter/base-notebook:latest

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless \
        tzdata \
        ca-certificates \
        maven && \
    rm -rf /var/lib/apt/lists/*

USER ${NB_UID}

RUN mamba create -y \
        -n spark310 \
        python=3.10 \
        pip \
        ipykernel \
        pandas \
        pyarrow && \
    mamba clean --all -f -y

RUN /opt/conda/envs/spark310/bin/python \
    -m pip install --no-cache-dir \
    "pyspark[sql]==4.2.0"

RUN /opt/conda/envs/spark310/bin/python \
    -m ipykernel install \
    --user \
    --name spark310 \
    --display-name "PySpark 4.2 - Python 3.10"

USER root

RUN set -eux; \
    PYSPARK_JARS="/opt/conda/envs/spark310/lib/python3.10/site-packages/pyspark/jars"; \
    HADOOP_JAR="$(find "${PYSPARK_JARS}" \
      -maxdepth 1 \
      -name 'hadoop-client-api-*.jar' \
      | head -1)"; \
    test -n "${HADOOP_JAR}"; \
    HADOOP_VERSION="$(basename "${HADOOP_JAR}" \
      | sed -E 's/^hadoop-client-api-(.+)\.jar$/\1/')"; \
    mvn \
      --batch-mode \
      dependency:copy-dependencies \
      -Dartifact="org.apache.hadoop:hadoop-aws:${HADOOP_VERSION}" \
      -DincludeScope=runtime \
      -DoutputDirectory=/tmp/s3a-jars; \
    cp -v /tmp/s3a-jars/*.jar "${PYSPARK_JARS}/"; \
    test -f "${PYSPARK_JARS}/hadoop-aws-${HADOOP_VERSION}.jar"; \
    rm -rf /tmp/s3a-jars /root/.m2

USER ${NB_UID}


docker-compose.yml:

Yaml
services:
  jupyterlab:
    build:
      context: .
      dockerfile: Dockerfile

    container_name: jupyterlab
    hostname: jupyterlab

    ports:
      - "8888:8888"
      - "127.0.0.1:4040:4040"

    volumes:
      - ./notebooks:/home/jovyan/work
      - /Users/vx53cd/.kube:/home/jovyan/.kube:ro

    environment:
      - JUPYTER_ENABLE_LAB=yes
      - KUBECONFIG=/home/jovyan/.kube/config
      - TZ=Europe/Amsterdam

    restart: unless-stopped

    command: start-notebook.sh --NotebookApp.token=''


Build and start JupyterLab:

Bash
docker compose down
docker compose build --no-cache
docker compose up -d


Verify S3A in Jupyter:

Bash
docker exec jupyterlab \
  sh -c '
    find /opt/conda/envs/spark310/lib/python3.10/site-packages/pyspark/jars \
      -maxdepth 1 \
      -name "hadoop-aws-*.jar" \
      -print
  '

HOW TO: Connect JupyterLab to the Spark cluster

This procedure uses JupyterLab as the Spark driver while Kubernetes runs the Spark executor pods.

1. Confirm that kind is running
Bash
kubectl config current-context
kubectl get nodes
kubectl get namespace spark


The context should be:

Text
kind-data-platform-lab


All three nodes should be Ready.

2. Export the kind CA certificate

Java must trust the certificate presented by the kind Kubernetes API server.

Run on the Mac:

Bash
kubectl config view \
  --raw \
  --minify \
  --flatten \
  -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 --decode \
  > /Users/vx53cd/.kube/kind-data-platform-lab-ca.crt


Verify it:

Bash
head -1 /Users/vx53cd/.kube/kind-data-platform-lab-ca.crt


Expected output:

Text
-----BEGIN CERTIFICATE-----

3. Recreate JupyterLab with the kubeconfig mount

The Compose configuration must include:

Yaml
volumes:
  - ./notebooks:/home/jovyan/work
  - /Users/vx53cd/.kube:/home/jovyan/.kube:ro


Recreate the container:

Bash
docker compose down
docker compose up -d --build

4. Connect JupyterLab to the kind network
Bash
docker network connect kind jupyterlab


If Docker says the endpoint already exists, the container is already connected.

Verify the connection:

Bash
docker inspect jupyterlab \
  --format '{{json .NetworkSettings.Networks}}'

5. Verify the mounted Kubernetes files
Bash
docker inspect jupyterlab \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'

docker exec jupyterlab \
  ls -l /home/jovyan/.kube/config

docker exec jupyterlab \
  ls -l /home/jovyan/.kube/kind-data-platform-lab-ca.crt


Verify Java and PySpark using the Python 3.10 environment:

Bash
docker exec jupyterlab java -version

docker exec jupyterlab \
  /opt/conda/envs/spark310/bin/python \
  -c "import sys, pyspark; print(sys.version); print(pyspark.__version__)"


Expected versions:

Text
Python 3.10
PySpark 4.2.0

6. Open JupyterLab

Open:

Text
http://127.0.0.1:8888


Select the kernel:

Text
PySpark 4.2 - Python 3.10


Restart the notebook kernel before creating the Kubernetes Spark session. This prevents reuse of an older local[2] SparkContext.

7. Create the Kubernetes Spark session

Run this as the first Spark cell. Supply the MinIO credentials through protected environment variables or another secure mechanism. Do not commit literal credentials in the notebook.

Python
import os
from pathlib import Path
from pyspark.sql import SparkSession

ca_file = "/home/jovyan/.kube/kind-data-platform-lab-ca.crt"
assert Path(ca_file).exists(), f"CA certificate not found: {ca_file}"

minio_user = os.environ["AWS_ACCESS_KEY_ID"]
minio_password = os.environ["AWS_SECRET_ACCESS_KEY"]

spark = (
    SparkSession.builder
    .master("k8s://https://data-platform-lab-control-plane:6443")
    .appName("jupyter-k8s-test")

    .config("spark.kubernetes.namespace", "spark")
    .config("spark.kubernetes.container.image", "data-platform-spark:latest")
    .config("spark.kubernetes.container.image.pullPolicy", "Never")
    .config("spark.kubernetes.authenticate.caCertFile", ca_file)
    .config(
        "spark.kubernetes.authenticate.driver.serviceAccountName",
        "spark",
    )

    .config("spark.executor.instances", "2")
    .config("spark.executor.cores", "1")
    .config("spark.executor.memory", "2g")

    .config("spark.driver.host", "jupyterlab")
    .config("spark.driver.port", "7078")
    .config("spark.driver.blockManager.port", "7079")

    .config(
        "spark.hadoop.fs.s3a.endpoint",
        "http://host.docker.internal:9000",
    )
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
    .config(
        "spark.hadoop.fs.s3a.impl",
        "org.apache.hadoop.fs.s3a.S3AFileSystem",
    )
    .config(
        "spark.hadoop.fs.s3a.aws.credentials.provider",
        "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
    )
    .config("spark.hadoop.fs.s3a.access.key", minio_user)
    .config("spark.hadoop.fs.s3a.secret.key", minio_password)

    .config("spark.sql.session.timeZone", "Europe/Amsterdam")

    .config("spark.ui.enabled", "true")
    .config("spark.ui.port", "4040")
    .config("spark.ui.bindAddress", "0.0.0.0")

    .getOrCreate()
)

8. Verify Kubernetes execution and S3A
Python
print("Master:", spark.sparkContext.master)
print("Application ID:", spark.sparkContext.applicationId)
print("Spark UI:", spark.sparkContext.uiWebUrl)
print("Timezone:", spark.conf.get("spark.sql.session.timeZone"))


The master must be:

Text
k8s://https://data-platform-lab-control-plane:6443


Verify that the driver can load S3A:

Python
jvm = spark.sparkContext._jvm

print(
    jvm.java.lang.Class.forName(
        "org.apache.hadoop.fs.s3a.S3AFileSystem"
    ).getName()
)


Expected:

Text
org.apache.hadoop.fs.s3a.S3AFileSystem

9. Run the test job
Python
from pyspark.sql.functions import expr

df = (
    spark.range(1, 1_000_000)
    .toDF("id")
    .repartition(10)
    .withColumn("square", expr("id * id"))
    .cache()
)

df.take(10)


Verify the number of partitions:

Python
df.rdd.getNumPartitions()


Expected result:

Text
10


Materialize the complete cache:

Python
df.count()

10. Write Parquet to MinIO

Create sample orders:

Python
from pyspark.sql import functions as F

orders = (
    spark.range(1_000_000, numPartitions=10)
    .withColumn("customer_id", F.col("id") % 100_000)
    .withColumn(
        "country",
        F.when(F.col("id") % 5 == 0, "NL")
         .when(F.col("id") % 5 == 1, "DE")
         .when(F.col("id") % 5 == 2, "IT")
         .when(F.col("id") % 5 == 3, "FR")
         .otherwise("BE"),
    )
    .withColumn(
        "amount",
        ((F.col("id") % 1000) + 1).cast("double"),
    )
)


Write partitioned Parquet:

Python
(
    orders.write
    .mode("overwrite")
    .partitionBy("country")
    .parquet("s3a://processed/orders")
)

11. Read Parquet from MinIO
Python
stored_orders = spark.read.parquet(
    "s3a://processed/orders"
)

stored_orders.printSchema()
stored_orders.show(20, truncate=False)
print("Rows:", stored_orders.count())


print(stored_orders) does not display DataFrame rows. Use show(), count(), or another action.

Verify the partition column:

Python
stored_orders.select("country").distinct().show()


Test partition pruning:

Python
nl_orders = (
    stored_orders
    .filter(F.col("country") == "NL")
    .select("customer_id", "amount")
)

nl_orders.explain("formatted")
nl_orders.show(20, truncate=False)

12. Check Kubernetes executor pods

Run from the Mac while the notebook Spark session is active:

Bash
kubectl get pods -n spark -o wide


For a live view:

Bash
kubectl get pods -n spark -w


In Jupyter client mode, JupyterLab hosts the driver and Kubernetes creates the executor pods.

13. Open the Spark UI and MinIO console

Spark UI:

Text
http://127.0.0.1:4040


MinIO console:

Text
http://127.0.0.1:9001


Useful Spark UI tabs:

Jobs: actions triggered by take(), count(), reads, and writes
Stages: shuffles introduced by repartitioning and aggregation
Executors: Kubernetes executor pods
Storage: cached DataFrames
SQL: DataFrame execution plans
14. Stop the Spark session
Python
spark.stop()


Then verify executor cleanup:

Bash
kubectl get pods -n spark

Troubleshooting
The notebook reports local[2]

An existing local SparkContext was reused. Stop Spark, restart the kernel, and create the Kubernetes session as the first Spark operation.

PKIX path building failed

Confirm that the CA file exists inside JupyterLab:

Bash
docker exec jupyterlab \
  ls -l /home/jovyan/.kube/kind-data-platform-lab-ca.crt


Confirm the Spark builder contains:

Python
.config(
    "spark.kubernetes.authenticate.caCertFile",
    "/home/jovyan/.kube/kind-data-platform-lab-ca.crt",
)

Executor pods cannot connect to the driver

Confirm that JupyterLab belongs to the kind Docker network:

Bash
docker network connect kind jupyterlab


Retain these driver settings:

Python
.config("spark.driver.host", "jupyterlab")
.config("spark.driver.port", "7078")
.config("spark.driver.blockManager.port", "7079")

Python version mismatch

The Python minor version must match between the Jupyter driver and Spark workers. Use the PySpark 4.2 - Python 3.10 kernel and verify Spark executors also use Python 3.10.

ClassNotFoundException: org.apache.hadoop.fs.s3a.S3AFileSystem

The process hosting the Spark driver is missing hadoop-aws and its compatible runtime dependencies.

In Jupyter client mode, install matching S3A dependencies in both:

Text
Jupyter image
Spark executor image


Then rebuild JupyterLab, restart the kernel, rebuild the Spark image, and reload it into kind.

listPathRaw: 0 drives provided

MinIO does not have a usable /data storage mount. Use and verify the Docker named volume:

Bash
docker volume inspect data-platform-minio-data

docker inspect minio \
  --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}'


Expected:

Text
volume data-platform-minio-data /data

MinIO pulls minio/minio:latest

The old workflow is running. Verify the current remote workflow and start a new workflow run from main rather than rerunning an old failed run:

Bash
git fetch origin

git show origin/main:.github/workflows/deploy-storage.yml \
  | grep -nE 'image:|force-recreate|9000:9000'

Spark UI is unavailable

Confirm that Compose publishes port 4040, recreate JupyterLab, reconnect it to kind, restart the notebook kernel, and recreate the Spark session.

Useful commands
Bash
kubectl get nodes
kubectl get pods -A
kubectl get pods -n spark -o wide
kubectl get serviceaccount,role,rolebinding -n spark

docker network inspect kind
docker port jupyterlab
docker exec jupyterlab java -version
docker exec jupyterlab \
  /opt/conda/envs/spark310/bin/python \
  -c "import sys, pyspark; print(sys.version); print(pyspark.__version__)"

docker ps --filter name=minio
docker logs --tail 100 minio
docker port minio
docker volume inspect data-platform-minio-data

Suggested next steps
Add Spark event logging to s3a://spark-events/.
Add a Spark History Server.
Use s3a://checkpoints/ for Structured Streaming checkpoints.
Add Delta Lake or Apache Iceberg after Parquet reads and writes are stable.
Create exercises for joins, partitioning, shuffles, caching, and data skew.
Add automated tests for PySpark transformations. EOF python - <<'PY' from pathlib import Path p=Path('/mnt/data/README.md') s=p.read_text() checks=[ '# Data Platform Lab', '# HOW TO: Connect JupyterLab to the Spark cluster', '## MinIO storage', 's3a://processed/orders', 'data-platform-minio-data', 'ClassNotFoundException: org.apache.hadoop.fs.s3a.S3AFileSystem', '- Add Spark event logging to s3a://spark-events/.', ] for c in checks: assert c in s, c print(f'{p.name}: {len(s.splitlines())} lines, {p.stat().st_size} bytes') PY