# Data Platform Lab

A local Spark data-platform lab on Apple Silicon using Docker Desktop, kind Kubernetes, Terraform, GitHub Actions, JupyterLab, Apache Spark 4.2, and MinIO-compatible S3 object storage.

## Architecture

```text
GitHub Actions on self-hosted ARM64 Mac
                |
                +--> deploy-spark-infra.yml
                |      Creates/reuses kind cluster
                |      Applies Terraform infrastructure
                |
                +--> deploy-storage.yml
                |      Creates/recreates MinIO container
                |      Creates S3 buckets
                |      Persists data in a Docker volume
                |
                +--> deploy-spark-cluster.yml
                       Builds Spark image with S3A
                       Loads image into kind
                       Applies RBAC and MinIO credentials
                       Submits Spark application

JupyterLab container
  Spark driver in client mode
          |
          | Kubernetes API
          v
kind Kubernetes cluster
  +-- control-plane
  +-- worker
  |    +-- Spark executor pod
  +-- worker2
       +-- Spark executor pod
          |
          | S3A
          v
MinIO S3 API
  host.docker.internal:9000
          |
          v
Persistent Docker volume
  data-platform-minio-data
```

## Components

- Apple Silicon Mac
- Docker Desktop
- Self-hosted GitHub Actions runner with the labels:
  - `self-hosted`
  - `macOS`
  - `ARM64`
- kind Kubernetes cluster named `data-platform-lab`
- Terraform Kubernetes provider
- Apache Spark 4.2.0
- PySpark 4.2.0
- Java 21
- Python 3.10 for the Jupyter driver and Spark Python workers
- JupyterLab
- MinIO-compatible S3 object storage

## Repository layout

```text
data-platform-lab/
├── .github/
│   └── workflows/
│       ├── deploy-spark-infra.yml
│       ├── deploy-spark-cluster.yml
│       ├── deploy-storage.yml
│       └── destroy-spark-cluster.yml
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

## GitHub repository secrets

Configure the following secrets under:

```text
Settings
→ Secrets and variables
→ Actions
→ Repository secrets
```

Required secrets:

```text
MINIO_ROOT_USER
MINIO_ROOT_PASSWORD
```

Never commit MinIO credentials to the repository, Dockerfiles, notebooks, or Terraform files.

If credentials are accidentally exposed, rotate `MINIO_ROOT_PASSWORD` and rerun the storage and Spark deployment workflows.

## Workflow order

For a clean deployment, run the workflows in this order:

```text
1. Deploy Spark Infrastructure
2. Deploy MinIO Storage
3. Deploy Spark Software
```

To delete Kubernetes compute:

```text
Destroy Spark kind Cluster
```

Deleting the kind cluster removes:

- the Kubernetes control plane;
- the Kubernetes worker nodes;
- Spark driver and executor pods;
- namespaces;
- services;
- ConfigMaps;
- in-cluster Kubernetes resources.

MinIO storage is managed separately and should survive kind cluster recreation.

# Spark infrastructure

The `deploy-spark-infra.yml` workflow:

1. Checks out the repository.
2. Ensures the `data-platform-lab` kind cluster exists.
3. Uses `kubernetes/kind-config.yaml`.
4. Selects the `kind-data-platform-lab` Kubernetes context.
5. Restores the most recent Terraform state.
6. Runs Terraform format, initialization, validation, planning, and apply.
7. Verifies Kubernetes resources.
8. Uploads Terraform state as a workflow artifact.

## Terraform working directory

The workflow has this job-level default:

```yaml
defaults:
  run:
    working-directory: terraform
```

Therefore, non-Terraform operations must explicitly run from the repository root:

```yaml
- name: Ensure kind cluster exists
  shell: bash
  working-directory: .
```

Without that override, this path:

```text
kubernetes/kind-config.yaml
```

would incorrectly be resolved as:

```text
terraform/kubernetes/kind-config.yaml
```

## kind configuration

`kubernetes/kind-config.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4

nodes:
  - role: control-plane
  - role: worker
  - role: worker
```

Create the cluster manually if needed:

```bash
kind create cluster \
  --name data-platform-lab \
  --config kubernetes/kind-config.yaml \
  --wait 300s
```

Verify it:

```bash
kubectl config current-context
kubectl get nodes -o wide
```

Expected context:

```text
kind-data-platform-lab
```

Expected nodes:

```text
data-platform-lab-control-plane
data-platform-lab-worker
data-platform-lab-worker2
```

# Spark RBAC

The Spark namespace contains a `spark` service account used by Spark driver pods.

Apply RBAC:

```bash
kubectl apply \
  -f kubernetes/spark/spark-rbac.yaml
```

Verify permissions:

```bash
kubectl auth can-i create pods \
  --as=system:serviceaccount:spark:spark \
  --namespace=spark

kubectl auth can-i create services \
  --as=system:serviceaccount:spark:spark \
  --namespace=spark

kubectl auth can-i create configmaps \
  --as=system:serviceaccount:spark:spark \
  --namespace=spark
```

Expected output:

```text
yes
yes
yes
```

# MinIO storage

The `deploy-storage.yml` workflow creates and starts a MinIO-compatible S3 service using Docker Compose.

## MinIO endpoints

```text
S3 API from the Mac:
http://127.0.0.1:9000

S3 API from Spark/kind:
http://host.docker.internal:9000

Administrative console from the Mac:
http://127.0.0.1:9001
```

The MinIO S3 API must not be bound only to `127.0.0.1`, because Kubernetes pods need to reach the API through Docker Desktop's host gateway.

Use:

```yaml
ports:
  - "9000:9000"
  - "127.0.0.1:9001:9001"
```

The administrative console remains restricted to the Mac.

## MinIO image

Do not use the unavailable Docker Hub images:

```text
minio/minio:latest
minio/mc:latest
```

The current lab uses a pinned ARM64-capable MinIO image:

```yaml
image: alpine/minio:RELEASE.2025-10-15T17-29-55Z
```

The workflow uses the Amazon AWS CLI container for bucket administration:

```text
amazon/aws-cli:2.38.3
```

## Buckets

The storage workflow creates these buckets idempotently:

```text
raw
processed
checkpoints
spark-events
```

Suggested usage:

```text
raw
  CSV, JSON, and original source datasets

processed
  Parquet and later Iceberg or Delta outputs

checkpoints
  Structured Streaming checkpoints

spark-events
  Spark event logs for a future Spark History Server
```

# Persistent MinIO data

Do not store important MinIO data inside the GitHub Actions repository checkout.

A self-hosted runner checkout can clean or replace repository directories. Use a Docker named volume instead.

Recommended Compose configuration:

```yaml
services:
  minio:
    image: alpine/minio:RELEASE.2025-10-15T17-29-55Z
    container_name: minio
    hostname: minio

    command:
      - server
      - /data
      - --console-address
      - ":9001"

    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
      TZ: Europe/Amsterdam

    ports:
      - "9000:9000"
      - "127.0.0.1:9001:9001"

    volumes:
      - minio-data:/data

    restart: unless-stopped

volumes:
  minio-data:
    name: data-platform-minio-data
```

The named volume survives:

- MinIO container recreation;
- kind cluster deletion;
- kind cluster recreation;
- GitHub Actions repository checkout cleanup.

Do not run the following command unless intentionally deleting all stored MinIO data:

```bash
docker compose down -v
```

The `-v` option deletes Compose-managed volumes.

## Verify the MinIO volume

```bash
docker volume inspect \
  data-platform-minio-data
```

Inspect the container mount:

```bash
docker inspect minio \
  --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}'
```

Expected:

```text
volume data-platform-minio-data /data
```

## Verify MinIO health

From the Mac:

```bash
curl -f \
  http://127.0.0.1:9000/minio/health/ready
```

Check the container:

```bash
docker ps --filter name=minio
docker logs --tail 100 minio
docker port minio
```

Expected port mappings:

```text
9000/tcp -> 0.0.0.0:9000
9001/tcp -> 127.0.0.1:9001
```

## Verify MinIO from Kubernetes

Create a temporary connectivity pod:

```bash
kubectl run minio-connectivity-test \
  --namespace spark \
  --image=curlimages/curl:8.12.1 \
  --restart=Never \
  --command -- \
  curl --fail --silent --show-error \
    http://host.docker.internal:9000/minio/health/ready
```

Check the pod:

```bash
kubectl get pod \
  -n spark \
  minio-connectivity-test
```

Inspect its output:

```bash
kubectl logs \
  -n spark \
  minio-connectivity-test
```

Remove it:

```bash
kubectl delete pod \
  -n spark \
  minio-connectivity-test
```

# Verify MinIO buckets

Set credentials in the current terminal without committing them:

```bash
export AWS_ACCESS_KEY_ID="<your MinIO username>"
export AWS_SECRET_ACCESS_KEY="<your MinIO password>"
export AWS_DEFAULT_REGION="us-east-1"
```

List buckets:

```bash
docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.38.3 \
  --endpoint-url http://host.docker.internal:9000 \
  s3api list-buckets
```

Expected buckets:

```text
raw
processed
checkpoints
spark-events
```

Test an object write:

```bash
echo "MinIO storage test" > /tmp/minio-test.txt
```

Upload it:

```bash
docker run --rm \
  -v /tmp:/aws \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.38.3 \
  --endpoint-url http://host.docker.internal:9000 \
  s3 cp \
  /aws/minio-test.txt \
  s3://raw/minio-test.txt
```

List the object:

```bash
docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.38.3 \
  --endpoint-url http://host.docker.internal:9000 \
  s3 ls s3://raw/
```

# Spark image with S3A support

Spark requires Hadoop's S3A connector to work with `s3a://` object-store paths.

The required implementation is:

```text
org.apache.hadoop.fs.s3a.S3AFileSystem
```

The Hadoop module is:

```text
hadoop-aws
```

The `hadoop-aws` version must match the Hadoop version included in the Spark runtime.

## Interactive versus cluster execution

Both runtime sides need compatible S3A dependencies.

```text
Cluster-mode spark-submit:

data-platform-spark image
├── Spark driver pod
└── Spark executor pods


Interactive Jupyter client mode:

JupyterLab image
└── Spark driver

data-platform-spark image
└── Spark executor pods
```

If only the executor image contains `hadoop-aws`, an interactive notebook can fail before tasks reach the executors:

```text
ClassNotFoundException:
org.apache.hadoop.fs.s3a.S3AFileSystem
```

That happens because the Jupyter container hosts the Spark driver.

## Verify the Spark executor image

```bash
docker run --rm \
  data-platform-spark:latest \
  sh -c '
    ls -l /opt/spark/jars/hadoop-aws-*.jar
  '
```

Verify the*S3A class:

```bash
docker run --r* \
  data-platform-spark:latest \
  sh -c '
    jar tf /opt/spark/jars/hadoop-aws-*.jar |
    grep org/apache/hadoop/fs/s3a/S3AFileSystem.class
  '
```

Expected:

```text
org/apache/hadoop/fs/s3a/S3AFileSystem.class
```

## Verify the Jupyter driver image

The current Jupyter PySpark environment is expected under:

```text
/opt/conda/envs/spark310
```

Find the S3A connector:

```bash
docker exec jupyterlab \
  sh -c '
    find \
      /opt/conda/envs/spark310/lib/python3.10/site-packages/pyspark/jars \
      -maxdepth 1 \
      -name "hadoop-aws-*.jar" \
      -print
  '
```

Veri*y the class:

```bash
docker exec *upyterlab \
  sh -c '
    jar tf \*      /opt/conda/envs/spark310/lib*python3.10/site-packages/pyspark/j*rs/hadoop-aws-*.jar |
    grep org/apache/hadoop/fs/s3a/S3AFileSystem.class
  '
```

If the connector is missing, rebuild the JupyterLab image and restart the container and notebook kernel.

# Connect JupyterLab to Spark on Kubernetes

Restart the Jupyter kernel before creating a new Spark application.

Do not create a local Spark session such as:

```python
.master("local[3]")
```

Use the Kubernetes master:

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .master(
        "k8s://https://data-platform-lab-control-plane:6443"
    )
    .appName("jupyter-minio-lab")

    .config(
        "spark.kubernetes.namespace",
        "spark"
    )
    .config(
        "spark.kubernetes.container.image",
        "data-platform-spark:latest"
    )
    .config(
        "spark.kubernetes.container.image.pullPolicy",
        "Never"
    )
    .config(
        "spark.kubernetes.authenticate.driver.serviceAccountName",
        "spark"
    )

    .config(
        "spark.executor.instances",
        "2"
    )
    .config(
        "spark.executor.cores",
        "1"
    )
    .config(
        "spark.executor.memory",
        "2g"
    )

    .config(
        "spark.driver.host",
        "jupyterlab"
    )
    .config(
        "spark.driver.port",
        "7078"
    )
    .config(
        "spark.driver.blockManager.port",
        "7079"
    )

    .config(
        "spark.hadoop.fs.s3a.endpoint",
        "http://host.docker.internal:9000"
    )
    .config(
        "spark.hadoop.fs.s3a.path.style.access",
        "true"
    )
    .config(
        "spark.hadoop.fs.s3a.connection.ssl.enabled",
        "false"
    )
    .config(
        "spark.hadoop.fs.s3a.impl",
        "org.apache.hadoop.fs.s3a.S3AFileSystem"
    )
    .config(
        "spark.hadoop.fs.s3a.aws.credentials.provider",
        "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider"
    )

    .config(
        "spark.sql.session.timeZone",
        "Europe/Amsterdam"
    )

    .config(
        "spark.ui.enabled",
        "true"
    )
    .config(
        "spark.ui.port",
        "4040"
    )
    .config(
        "spark.ui.bindAddress",
        "0.0.0.0"
    )

    .getOrCreate()
)
```

Provide MinIO credentials through environment variables, a Kubernetes Secret, or protected runtime configuration.

Do not commit literal credentials to the notebook or README.

## Verify the Spark application

```python
print(
    "Master:",
    spark.sparkContext.master
)

print(
    "Application:",
    spark.sparkContext.applicationId
)

print(
    "UI:",
    spark.sparkContext.uiWebUrl
)

print(
    "Timezone:",
    spark.conf.get(
        "spark.sql.session.timeZone"
    )
)
```

Expected master:

```text
k8s://https://data-platform-lab-control-plane:6443
```

## Verify S3A in the driver

```python
jvm = spark.sparkContext._jvm

s3a_class = jvm.java.lang.Class.forName(
    "org.apache.hadoop.fs.s3a.S3AFileSystem"
)

print(
    s3a_class.getName()
)
```

Expected:

```text
org.apache.hadoop.fs.s3a.S3AFileSystem
```

Verify Hadoop filesystem resolution:

```python
path = jvm.org.apache.hadoop.fs.Path(
    "s3a://processed/orders"
)

filesystem = path.getFileSystem(
    spark.sparkContext
         ._jsc
         .hadoopConfiguration()
)

print(
    filesystem.getClass().getName()
)
```

Expected:

```text
org.apache.hadoop.fs.s3a.S3AFileSystem
```

# Write Parquet to MinIO

Generate synthetic data:

```python
from pyspark.sql import functions as F

orders = (
    spark.range(
        1_000_000,
        numPartitions=10
    )
    .withColumn(
        "customer_id",
        F.col("id") % 100_000
    )
    .withColumn(
        "country",
        F.when(
            F.col("id") % 5 == 0,
            "NL"
        )
        .when(
            F.col("id") % 5 == 1,
            "DE"
        )
        .when(
            F.col("id") % 5 == 2,
            "IT"
        )
        .when(
            F.col("id") % 5 == 3,
            "FR"
        )
        .otherwise("BE")
    )
    .withColumn(
        "amount",
        (
            (F.col("id") % 1000) + 1
        ).cast("double")
    )
)
```

Write partitioned Parquet:

```python
(
    orders.write
    .mode("overwrite")
    .partitionBy("country")
    .parquet(
        "s3a://processed/orders"
    )
)
```

# Read Parquet from MinIO

```python
stored_orders = spark.read.parquet(
    "s3a://processed/orders"
)
```

Print the schema:

```python
stored_orders.printSchema()
```

Display rows:

```python
stored_orders.show(
    20,
    truncate=False
)
```

This does not display rows:

```python
print(stored_orders)
```

It only prints the DataFrame representation.

Use an action to retrieve or compute data:

```python
stored_orders.show()
stored_orders.count()
stored_orders.take(10)
```

Count the dataset:

```python
print(
    "Rows:",
    stored_orders.count()
)
```

Expected count for the sample dataset:

```text
1000000
```

Display the country partitions:

```python
stored_orders \
    .select("country") \
    .distinct() \
    .show()
```

Expected values:

```text
BE
DE
FR
IT
NL
```

# Partition pruning exercise

```python
nl_orders = (
    stored_orders
    .filter(
        F.col("country") == "NL"
    )
    .select(
        "customer_id",
        "amount"
    )
)
```

Inspect the physical plan:

```python
nl_orders.explain(
    "formatted"
)
```

Execute:

```python
nl_orders.show(
    20,
    truncate=False
)
```

Look for a partition filter based on `country`.

# Verify Parquet objects with AWS CLI

```bash
docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION=us-east-1 \
  amazon/aws-cli:2.38.3 \
  --endpoint-url http://host.docker.internal:9000 \
  s3 ls \
  s3://processed/orders/ \
  --recursive
```

Expected keys include paths similar to:

```text
orders/country=NL/part-....snappy.parquet
orders/country=DE/part-....snappy.parquet
orders/country=IT/part-....snappy.parquet
orders/country=FR/part-....snappy.parquet
orders/country=BE/part-....snappy.parquet
```

# Timezones

Different layers can display different timezone representations:

```text
Mac
  Europe/Amsterdam

kind node containers
  UTC

Kubernetes API timestamps
  UTC with trailing Z

Spark executor containers
  Europe/Amsterdam when configured

Spark SQL timestamps
  Controlled by spark.sql.session.timeZone
```

These can represent the same instant.

During Central European Summer Time:

```text
16:00 UTC = 18:00 CEST
```

A Kubernetes creation timestamp such as:

```text
2026-10-21T16:09:21Z
```

is intentionally returned in UTC.

Changing the container timezone does not change Kubernetes API metadata timestamps.

# Troubleshooting

## `spark` is not defined

A Jupyter kernel restart removes Python variables.

Recreate the Kubernetes SparkSession before running:

```python
spark.createDataFrame(...)
```

## Spark reports `local[2]` or `local[3]`

An existing local SparkContext was reused.

Run:

```python
spark.stop()
```

Then restart the kernel and create the Kubernetes SparkSession as the first Spark operation.

## Py4J `ConnectionRefusedError`

The Java Spark process connected to the current notebook is no longer running.

Restart the Jupyter kernel and create a fresh SparkSession.

## Driver or Spark UI ports are busy

Warnings such as these indicate an older process still owns the ports:

```text
sparkDriver could not bind on port 7078
SparkUI could not bind on port 4040
```

Restart the notebook kernel or JupyterLab container.

Use one SparkContext per notebook kernel.

## Python version mismatch

Python UDFs require compatible Python minor versions between driver and workers.

Current target:

```text
Jupyter driver: Python 3.10
Spark workers:  Python 3.10
```

## Arrow warning

Spark 4.2 may attempt Arrow optimization for Python UDFs.

Install compatible versions of:

```text
pandas
pyarrow
```

in both the Jupyter driver image and Spark executor image if using Arrow-enabled Python UDFs.

## Local file exists only in Jupyter

A path such as:

```text
/home/jovyan/work/data/lookup.csv
```

exists only in the Jupyter container unless deliberately mounted into every executor.

For distributed reads, use shared storage:

```python
spark.read.csv(
    "s3a://raw/lookup.csv"
)
```

For a tiny lookup, another option is to read it with Python on the driver and broadcast the resulting dictionary.

## `ClassNotFoundException: S3AFileSystem`

Error:

```text
Class org.apache.hadoop.fs.s3a.S3AFileSystem not found
```

The process hosting the Spark driver is missing `hadoop-aws` and its compatible AWS SDK dependencies.

In Jupyter client mode, install S3A dependencies in both:

```text
JupyterLab image
data-platform-spark image
```

Rebuild JupyterLab and restart the notebook kernel.

## S3A connector missing from the Spark image

Verify:

```bash
docker run --rm \
  data-platform-spark:latest \
  sh -c '
    ls -l /opt/spark/jars/hadoop-aws-*.jar
  '
```

If missing, update `spark/Dockerfile`, rebuild without cache, and load the image into kind again.

```bash
docker build \
  --no-cache \
  --progress=plain \
  --platform linux/arm64 \
  -t data-platform-spark:latest \
  ./spark

kind load docker-image \
  data-platform-spark:latest \
  --name data-platform-lab
```

Existing Spark pods continue using the old image. Stop the existing application and create new pods.

## MinIO returns `listPathRaw: 0 drives provided`

MinIO does not have a usable storage backend mounted at `/data`.

Check:

```bash
docker logs --tail 200 minio

docker inspect minio \
  --format '{{json .Mounts}}' |
  python3 -m json.tool

docker exec minio \
  sh -c '
    id
    ls -ld /data
    ls -la /data
    df -h /data
  '
```

Use a Docker nam*d volume:

```yaml
volumes:
  - mi*io-data:/data

volumes:
  minio-da*a:
    name: data-platform-minio-d*ta
```

Then recreate the MinIO co*tainer without deleting the volume*

## Workflow still pulls `minio/m*nio:latest`

The workflow run is u*ing an old commit or old branch.

*erify the remote workflow:

```bas*
git fetch origin

git show \
  or*gin/main:.github/workflows/deploy-*torage.yml |
  grep -nE \
    'ima*e:|force-recreate|*000:9000'
```

Start a new run fro* the updated `main` branch.

Do no* rerun an old historical workflow*run if the old run points to an ol*er commit.

## Kubernetes nodes*show UTC

*his is normal:

```bash
docker exe* \
  data-platform-lab-control-pla*e \
  date
```

kind nodes use the*`kindest/node`*image and can remain*on UTC.

Your Spark Dockerfile onl* changes*Spark containers, not kind node co*tainers.

## Kubernetes*creation timestamps show UTC*
This command always returns Kuber*etes API metadata in UTC:

```bash*kubectl get pod \
  -n spark \
  -* jsonpath='{.items[0].metadata.cre*tionTimestamp}{"\n"}'
```

A trail*ng `Z` means UTC.

# Useful comman*s

## Kubernetes

```bash
kubectl *onfig current-context

kubectl get*nodes -o wide

kubectl*get namespaces

kubectl get pods -* spark -o wide

kubectl get pods -*
```

## Spark image*
```bash
docker run --rm \
  data-*latform-spark:latest \
  sh -c '
 *  ls -l /opt/spark/jars/hadoop-aws**.jar
  '
```

## Jupyter versions*
```bash
docker exec jupyterlab \
* /opt/conda/envs/spark310/bin/pyth*n \
  -c '
*mport sys
import pyspark

*rint(sys.version)
print(pyspark.__*ersion*_)
'
```

## MinIO

```bash
docker*ps --filter name=minio*
docker logs --tail 100 minio

doc*er port minio

docker volume inspe*t \
  data-platform-minio-data

cu*l -f \
  http://127.0.0.1:9000/min*o/health/ready
```

## Spark UI

O*en:

```text
http://127.0.0.1:4040*```

Useful tabs:

```text
Jobs
St*ges
Storage
Executors
SQL
Environm*nt
```

## MinIO console

Open:

`*`text
http://127.0.0.1:9001*```

# Next steps

1. Move S3A cre*entials completely out of Spark co*mand-line configuration.
2. Inject*standard AWS environment variables*into Spark driver and executor pod*:
   - `AWS_ACCESS_KEY_ID`
   - `A*S_SECRET_ACCESS_KEY`
3. Add Spark *vent logging to:

```text
s3a://sp*rk-events/
```

4. Deploy a Spark *istory Server.
5. Store Structured*Streaming checkpoints under:

```t*xt
s3a://checkpoints/
```

6. Add * raw CSV-to-partitioned-Parquet pi*eline:

```text
s3a://raw*
    ↓
Spark transformations
    ↓*s3a://processed/
```

7. Add Apach* Iceberg or Delta Lake after basic*Parquet operations are stable.
```**