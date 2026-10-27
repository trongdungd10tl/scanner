# Adds NudeNet to the scanner image already loaded on the server, without the
# internet and without shipping the 1.3GB image again.
#
# The server cannot reach PyPI, so the wheels are fetched on a machine that can
# and copied here (see get_wheels.sh). The result keeps the same name, so
# docker-compose.yml needs no change. Build with --network host: on
# kakoak-app03 Docker cannot create bridge networking (the DOCKER iptables chain
# is gone), and nothing here needs the network anyway.
#
#   sudo docker build --network host -t moderation-scanner:latest .
#
# A full rebuild from the main Dockerfile installs the same pinned version and
# makes this layer unnecessary.
FROM moderation-scanner:latest

USER root
COPY wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels nudenet==3.4.2 \
    && rm -rf /tmp/wheels \
    && python -c "import torch, transformers; from nudenet import NudeDetector; NudeDetector(); print('nudenet ok')"
# uid rather than name: the scanner runs as 10001 (see docker-compose.yml)
USER 10001
