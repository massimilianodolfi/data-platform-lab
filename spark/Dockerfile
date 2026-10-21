FROM spark:4.2.0-python3

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Amsterdam
ENV JAVA_TOOL_OPTIONS="-Duser.timezone=Europe/Amsterdam"

COPY jobs/ /opt/spark/jobs/

USER 185