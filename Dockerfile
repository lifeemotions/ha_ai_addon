# Home Assistant Add-on Dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Copy application files
COPY run.sh /
COPY const.py /app/
COPY main.py /app/

# Set working directory
WORKDIR /app

# Make run script executable
RUN chmod a+x /run.sh

# Set entrypoint
CMD [ "/run.sh" ]
