# Data Platform Lab

A local data-platform engineering lab running on an Apple Silicon Mac. It combines GitHub Actions, a self-hosted runner, Terraform, Docker, kind Kubernetes, Apache Spark, PySpark, and JupyterLab.

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
                              |
                    Kubernetes workers

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

Install the required tools:

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install git gh kubectl kind helm jq python
```

Docker Desktop must be installed and running.

Verify the tools:

```bash
docker version
terraform version
kubectl version --client
kind version
gh --version
```

## Create the Kubernetes cluster

The `kubernetes/kind-config.yaml` file defines a three-node cluster:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4

nodes:
  - role: control-plane
  - role: worker
  - role: worker
```

Create the cluster:

```bash
kind create cluster \
  --name data-platform-lab \
  --config kubernetes/kind-config.yaml
```

Verify it:

```bash
kubectl config current-context
kubectl get nodes
```

Expected context:

```text
kind-data-platform-lab
```

## Terraform infrastructure

Terraform creates the Kubernetes namespaces used by the lab:

- `spark`
- `storage`

Run manually when required:

```bash
cd terraform
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

Verify:

```bash
kubectl get namespaces
terraform state list
```

Do not commit Terraform state:

```gitignore
**/.terraform/*
*.tfstate
*.tfstate.*
*.tfplan
crash.log
```

## GitHub Actions self-hosted runner

The runner is installed outside the repository, for example:

```text
~/github-runners/actions-runner/
```

The workflows target the Mac runner with:

```yaml
runs-on: [self-hosted, macOS, ARM64]
```

Keep the runner listening when Actions jobs need to run:

```bash
cd ~/github-runners/actions-runner
./run.sh
```

## Spark image

The Spark image packages PySpark jobs for Kubernetes.

`spark/Dockerfile`:

```dockerfile
FROM spark:4.2.0-python3

USER root
COPY jobs/ /opt/spark/jobs/
USER 185
```

Build it locally:

```bash
docker build \
  --platform linux/arm64 \
  -t data-platform-spark:latest \
  ./spark
```

Load it into every kind node:

```bash
kind load docker-image \
  data-platform-spark:latest \
  --name data-platform-lab
```

## Spark RBAC

The Spark service account needs permission to manage driver and executor resources in the `spark` namespace.

The role should include `deletecollection`, because Spark uses collection deletion during cleanup.

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

Verify permissions:

```bash
kubectl auth can-i create pods \
  --as=system:serviceaccount:spark:spark \
  --namespace=spark

kubectl auth can-i deletecollection pods \
  --as=system:serviceaccount:spark:spark \
  --namespace=spark
```

## Example Spark job

`spark/jobs/hello_spark.py`:

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("data-platform-lab")
    .getOrCreate()
)

df = spark.range(0, 10_000_000)

print(f"Count: {df.count()}")

(
    df.groupBy((df.id % 10).alias("bucket"))
      .count()
      .orderBy("bucket")
      .show()
)

spark.stop()
```

A successful Kubernetes run creates one driver and two executors:

```bash
kubectl get pods -n spark -o wide
```

Inspect the latest driver log:

```bash
DRIVER=$(kubectl get pods \
  -n spark \
  -l spark-role=driver \
  --sort-by=.metadata.creationTimestamp \
  -o name | tail -1)

kubectl logs -n spark "$DRIVER"
```

## JupyterLab image

Use a custom Jupyter image so Java and PySpark survive container recreation.

### Jupyter Dockerfile

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

### Jupyter Compose configuration

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
      - ~/.kube:/home/jovyan/.kube:ro

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

Connect JupyterLab to the kind Docker network:

```bash
docker network connect kind jupyterlab
```

Verify the runtime:

```bash
docker exec jupyterlab java -version
docker exec jupyterlab python -c "import pyspark; print(pyspark.__version__)"
docker port jupyterlab
```

Open JupyterLab:

```text
http://127.0.0.1:8888
```

## Connect a notebook to Spark on Kubernetes

In this mode, JupyterLab is the Spark driver and Kubernetes runs the executors.

Restart the notebook kernel before creating a new Spark context.

```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = (
    SparkSession.builder
    .master("k8s://https://data-platform-lab-control-plane:6443")
    .appName("jupyter-distributed-test")
    .config("spark.kubernetes.namespace", "spark")
    .config("spark.kubernetes.container.image", "data-platform-spark:latest")
    .config("spark.kubernetes.container.image.pullPolicy", "Never")
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

print("Spark version :", spark.version)
print("Master        :", spark.sparkContext.master)
print("Application   :", spark.sparkContext.appName)
print("Application ID:", spark.sparkContext.applicationId)
print("Spark UI      :", spark.sparkContext.uiWebUrl)
```

Confirm that the master begins with:

```text
k8s://
```

## Distributed notebook test

```python
df = (
    spark.range(20_000_000)
    .withColumn("customer_group", col("id") % 10)
    .withColumn("amount", (col("id") % 1000) + 1)
)

print("Partitions:", df.rdd.getNumPartitions())

result = (
    df.groupBy("customer_group")
      .agg({"amount": "sum"})
      .orderBy("customer_group")
)

result.show()
```

While the Spark session is running, inspect the executor pods:

```bash
kubectl get pods -n spark -o wide
```

## Spark UI

The Spark UI is exposed from the Jupyter driver container:

```text
http://127.0.0.1:4040
```

Validate the published ports:

```bash
docker port jupyterlab
```

Expected mappings include:

```text
4040/tcp -> 127.0.0.1:4040
8888/tcp -> 0.0.0.0:8888
```

The UI remains available only while the notebook SparkContext is alive.

Useful tabs include:

- Jobs
- Stages
- SQL
- Storage
- Environment
- Executors

## Stop the notebook Spark application

```python
spark.stop()
```

Spark should then remove the executor resources. If cleanup returns RBAC errors, verify that the service account has `deletecollection` permission for pods, services, ConfigMaps, and persistent volume claims.

## Useful commands

```bash
# Cluster status
kubectl get nodes
kubectl get pods -A
kubectl get pods -n spark -o wide

# Spark resources
kubectl get serviceaccount,role,rolebinding -n spark

# Docker networks
docker network inspect kind
docker inspect jupyterlab --format '{{json .NetworkSettings.Networks}}'

# Jupyter runtime
docker exec jupyterlab java -version
docker exec jupyterlab python -c "import pyspark; print(pyspark.__version__)"

# Terraform
cd terraform
terraform fmt -check
terraform validate
terraform plan
terraform state list
```

## Current milestone

The lab supports both execution patterns:

1. **Cluster mode through GitHub Actions**
   - GitHub Actions runs `spark-submit`.
   - Kubernetes creates the Spark driver and executor pods.

2. **Interactive client mode through JupyterLab**
   - JupyterLab hosts the Spark driver.
   - Kubernetes creates executor pods on the kind workers.
   - The Spark UI is available locally on port 4040.

## Suggested next steps

- Add Spark event logging and a Spark History Server.
- Add MinIO for S3-compatible object storage.
- Read and write Parquet datasets.
- Add Delta Lake or Apache Iceberg.
- Create exercises for joins, partitioning, shuffles, caching, and data skew.
- Add automated tests for PySpark transformations.
