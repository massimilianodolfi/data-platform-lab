FROM spark:4.2.0-python3

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Amsterdam
ENV JAVA_TOOL_OPTIONS="-Duser.timezone=Europe/Amsterdam"

# Install timezone data and Maven.
# Maven resolves hadoop-aws and its compatible runtime dependencies.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
        curl \
        maven && \
    ln -snf \
        /usr/share/zoneinfo/Europe/Amsterdam \
        /etc/localtime && \
    echo "Europe/Amsterdam" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# Detect the Hadoop version already bundled with this Spark image.
RUN set -eux; \
    HADOOP_JAR="$(find /opt/spark/jars \
        -maxdepth 1 \
        -name 'hadoop-client-api-*.jar' \
        | head -1)"; \
    test -n "${HADOOP_JAR}"; \
    HADOOP_VERSION="$(basename "${HADOOP_JAR}" \
        | sed -E \
          's/^hadoop-client-api-(.+)\.jar$/\1/')"; \
    echo "Detected Hadoop version: ${HADOOP_VERSION}"; \
    printf '%s\n' \
      '<project xmlns="http://maven.apache.org/POM/4.0.0"' \
      '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' \
      '         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">' \
      '  <modelVersion>4.0.0</modelVersion>' \
      '  <groupId>local.spark</groupId>' \
      '  <artifactId>spark-s3a-dependencies</artifactId>' \
      '  <version>1.0.0</version>' \
      '  <dependencies>' \
      '    <dependency>' \
      '      <groupId>org.apache.hadoop</groupId>' \
      '      <artifactId>hadoop-aws</artifactId>' \
      "      <version>${HADOOP_VERSION}</version>" \
      '    </dependency>' \
      '  </dependencies>' \
      '</project>' \
      > /tmp/s3a-pom.xml; \
    mvn \
      --batch-mode \
      --file /tmp/s3a-pom.xml \
      dependency:copy-dependencies \
      -DincludeScope=runtime \
      -DoutputDirectory=/tmp/s3a-jars; \
    cp -v /tmp/s3a-jars/*.jar /opt/spark/jars/; \
    test -f "/opt/spark/jars/hadoop-aws-${HADOOP_VERSION}.jar"; \
    rm -rf \
      /tmp/s3a-pom.xml \
      /tmp/s3a-jars \
      /root/.m2

COPY jobs/ /opt/spark/jobs/

USER 185