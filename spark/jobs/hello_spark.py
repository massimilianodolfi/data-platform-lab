from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("data-platform-lab")
    .getOrCreate()
)

print("=" * 60)
print("DATA PLATFORM LAB - SPARK TEST")
print("=" * 60)

df = spark.range(0, 10_000_000)

print(f"Count: {df.count()}")

df.groupBy((df.id % 10).alias("bucket")) \
    .count() \
    .orderBy("bucket") \
    .show()

print("=" * 60)
print("SPARK JOB COMPLETED SUCCESSFULLY")
print("=" * 60)

spark.stop()
