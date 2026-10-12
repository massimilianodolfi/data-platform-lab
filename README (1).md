# Data Platform Lab

A local data-platform engineering lab for Apple Silicon, combining GitHub Actions, a self-hosted runner, Terraform, Docker, kind Kubernetes, Apache Spark, PySpark, and JupyterLab.

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
       |                             |
       +--------------+--------------+
                      |
                      v
                spark namespace
                      |
              +-------+-------+
              |               |
         Spark driver     Spark executors

JupyterLab container
       |
       | Spark client mode
       v
Notebook Spark driver
       |
       v
Executors on kind Kubernetes
```

## Components

- Apple Silicon Mac with 48 GB memory
- Docker Desktop
- kind Kubernetes cluster with one control-plane and two worker nodes
- Terraform Kubernetes provider
- GitHub Actions self-hosted ARM64 runner
- Apache Spark 4.2.0
- PySpark 4.2.0
- Java 21
- JupyterLab

## Repository structure

```text
data-platform-lab/
├── .github/
│   └── workflows/
│       ├── deploy-spark-cluster.yml
│       ├── deploy-spark-infra.yml
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
├── terraform/
│   ├── main.tf
│   ├── outputs.tf
│   ├── providers.tf
│   ├── variables.tf
│   └── versions.tf
└── README.md
```

## Prerequisites

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install git gh kubectl kind helm jq python
```

Docker Desktop must be installed and running.

## Create the Kubernetes cluster

`kubernetes/kind-config.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4

nodes:
  - role: control-plane
  - role: worker
  - role: worker
```

Create and verify the cluster:

```bash
kind create cluster \
  --name data-platform-lab \
  --config kubernetes/kind-config.yaml

kubectl config current-context
kubectl get nodes
```

Expected context:

```text
kind-data-platform-lab
```

## Terraform infrastructure

Terraform manages the `spark` and `storage` namespaces.

```bash
cd terraform
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

Do not commit Terraform state:

```gitignore
**/.terraform/*
*.tfstate
*.tfstate.*
*.tfplan
crash.log
```

## Spark image

`spark/Dockerfile`:

```dockerfile
FROM spark:4.2.0-python3

USER root
COPY jobs/ /opt/spark/jobs/
USER 185
```

Build and load the image into kind:

```bash
docker build \
  --platform linux/arm64 \
  -t data-platform-spark:latest \
  ./spark

kind load docker-image \
  data-platform-spark:latest \
  --name data-platform-lab
```

## Spark RBAC

`kubernetes/spark/spark-rbac.yaml` should grant the Spark service account permission to manage Spark resources:

```yaml
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
```

Apply it:

```bash
kubectl apply -f kubernetes/spark/spark-rbac.yaml
```

## JupyterLab image

Create the JupyterLab image with Java and PySpark already installed.

`Dockerfile`:

```dockerfile
FROM quay.io/jupyter/base-notebook:latest

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless && \
    rm -rf /var/lib/apt/lists/*

USER ${NB_UID}

RUN python -m pip install --no-cache-dir \
    pyspark==4.2.0
```

`docker-compose.yml`:

```yaml
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

    restart: unless-stopped

    command: start-notebook.sh --NotebookApp.token=''
```

Build and start JupyterLab:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

---

# HOW TO: Connect JupyterLab to the Spark cluster

This procedure uses JupyterLab as the Spark driver while Kubernetes runs the Spark executor pods.

## 1. Confirm that kind is running

```bash
kubectl config current-context
kubectl get nodes
kubectl get namespace spark
```

The context should be:

```text
kind-data-platform-lab
```

All three nodes should be `Ready`.

## 2. Export the kind CA certificate

Java must trust the certificate presented by the kind Kubernetes API server.

Run on the Mac:

```bash
kubectl config view \
  --raw \
  --minify \
  --flatten \
  -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 --decode \
  > /Users/vx53cd/.kube/kind-data-platform-lab-ca.crt
```

Verify it:

```bash
head -1 /Users/vx53cd/.kube/kind-data-platform-lab-ca.crt
```

Expected output:

```text
-----BEGIN CERTIFICATE-----
```

## 3. Recreate JupyterLab with the kubeconfig mount

The Compose configuration must include:

```yaml
volumes:
  - ./notebooks:/home/jovyan/work
  - /Users/vx53cd/.kube:/home/jovyan/.kube:ro
```

Recreate the container:

```bash
docker compose down
docker compose up -d --build
```

## 4. Connect JupyterLab to the kind network

```bash
docker network connect kind jupyterlab
```

If Docker says the endpoint already exists, the container is already connected.

Verify the connection:

```bash
docker inspect jupyterlab \
  --format '{{json .NetworkSettings.Networks}}'
```

## 5. Verify the mounted Kubernetes files

```bash
docker inspect jupyterlab \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'

docker exec jupyterlab \
  ls -l /home/jovyan/.kube/config

docker exec jupyterlab \
  ls -l /home/jovyan/.kube/kind-data-platform-lab-ca.crt
```

Verify Java and PySpark:

```bash
docker exec jupyterlab java -version

docker exec jupyterlab \
  python -c "import pyspark; print(pyspark.__version__)"
```

Expected PySpark version:

```text
4.2.0
```

## 6. Open JupyterLab

Open:

```text
http://127.0.0.1:8888
```

Restart the notebook kernel before creating the Kubernetes Spark session. This prevents reuse of an older `local[2]` SparkContext.

## 7. Create the Kubernetes Spark session

Run this as the first Spark cell:

```python
from pathlib import Path
from pyspark.sql import SparkSession

ca_file = "/home/jovyan/.kube/kind-data-platform-lab-ca.crt"
assert Path(ca_file).exists(), f"CA certificate not found: {ca_file}"

spark = (
    SparkSession.builder
    .master("k8s://https://data-platform-lab-control-plane:6443")
    .appName("jupyter-k8s-test")
    .config("spark.kubernetes.namespace", "spark")
    .config(
        "spark.kubernetes.container.image",
        "data-platform-spark:latest",
    )
    .config(
        "spark.kubernetes.container.image.pullPolicy",
        "Never",
    )
    .config(
        "spark.kubernetes.authenticate.caCertFile",
        ca_file,
    )
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
    .config("spark.ui.enabled", "true")
    .config("spark.ui.port", "4040")
    .config("spark.ui.bindAddress", "0.0.0.0")
    .getOrCreate()
)
```

## 8. Verify Kubernetes execution

```python
print("Master:", spark.sparkContext.master)
print("Application ID:", spark.sparkContext.applicationId)
print("Spark UI:", spark.sparkContext.uiWebUrl)
```

The master must be:

```text
k8s://https://data-platform-lab-control-plane:6443
```

If it displays `local[2]`, run:

```python
spark.stop()
```

Then restart the notebook kernel and create the Kubernetes Spark session again as the first Spark operation.

## 9. Run the test job

```python
from pyspark.sql.functions import expr

df = (
    spark.range(1, 1_000_000)
    .toDF("id")
    .repartition(10)
    .withColumn("square", expr("id * id"))
    .cache()
)

df.take(10)
```

Verify the number of partitions:

```python
df.rdd.getNumPartitions()
```

Expected result:

```text
10
```

Materialize the complete cache:

```python
df.count()
```

## 10. Check the Kubernetes executor pods

Run from the Mac while the notebook Spark session is active:

```bash
kubectl get pods -n spark -o wide
```

For a live view:

```bash
kubectl get pods -n spark -w
```

In Jupyter client mode, JupyterLab hosts the driver and Kubernetes creates the executor pods.

## 11. Open the Spark UI

Open:

```text
http://127.0.0.1:4040
```

Useful tabs for this test:

- **Jobs**: actions triggered by `take()` and `count()`
- **Stages**: the shuffle introduced by `repartition(10)`
- **Executors**: the Kubernetes executor pods
- **Storage**: the cached DataFrame after `count()`
- **SQL**: the DataFrame query plans

Verify Docker's port mapping:

```bash
docker port jupyterlab
```

Expected mapping:

```text
4040/tcp -> 127.0.0.1:4040
```

## 12. Stop the Spark session

```python
spark.stop()
```

Then verify executor cleanup:

```bash
kubectl get pods -n spark
```

## Troubleshooting

### The notebook reports `local[2]`

An existing local SparkContext was reused. Stop Spark, restart the kernel, and create the Kubernetes session as the first Spark operation.

### `PKIX path building failed`

Confirm that the CA file exists inside JupyterLab:

```bash
docker exec jupyterlab \
  ls -l /home/jovyan/.kube/kind-data-platform-lab-ca.crt
```

Confirm the Spark builder contains:

```python
.config(
    "spark.kubernetes.authenticate.caCertFile",
    "/home/jovyan/.kube/kind-data-platform-lab-ca.crt",
)
```

### Executor pods cannot connect to the driver

Confirm that JupyterLab belongs to the `kind` Docker network:

```bash
docker network connect kind jupyterlab
```

Retain these driver settings:

```python
.config("spark.driver.host", "jupyterlab")
.config("spark.driver.port", "7078")
.config("spark.driver.blockManager.port", "7079")
```

### Spark UI is unavailable

Confirm that Compose publishes port 4040, recreate JupyterLab, reconnect it to `kind`, restart the notebook kernel, and recreate the Spark session.

## Useful commands

```bash
kubectl get nodes
kubectl get pods -A
kubectl get pods -n spark -o wide
kubectl get serviceaccount,role,rolebinding -n spark

docker network inspect kind
docker port jupyterlab
docker exec jupyterlab java -version
docker exec jupyterlab python -c "import pyspark; print(pyspark.__version__)"
```

## Suggested next steps

- Add Spark event logging and a Spark History Server.
- Add MinIO for S3-compatible object storage.
- Read and write Parquet datasets.
- Add Delta Lake or Apache Iceberg.
- Create exercises for joins, partitioning, shuffles, caching, and data skew.
- Add automated tests for PySpark transformations.
