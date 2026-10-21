FROM spark:4.2.0-python3

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Amsterdam
ENV JAVA_TOOL_OPTIONS="-Duser.timezone=Europe/Amsterdam"

RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    ln -snf /usr/share/zoneinfo/Europe/Amsterdam /etc/localtime && \
    echo "Europe/Amsterdam" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

COPY jobs/ /opt/spark/jobs/

USER 185
