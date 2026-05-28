FROM ubuntu:22.04

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SHELL=/bin/sh

COPY scripts/run-pdocker-test-suite.sh /usr/local/bin/run-pdocker-test-suite
COPY scripts/start-pdocker-test-suite.sh /usr/local/bin/start-pdocker-test-suite
COPY scripts/pdocker-container-probe.sh /usr/local/bin/pdocker-container-probe
RUN ln -s /usr/local/bin/run-pdocker-test-suite /usr/local/bin/run-skydnir-test-suite \
    && ln -s /usr/local/bin/start-pdocker-test-suite /usr/local/bin/start-skydnir-test-suite

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD test -f /reports/ready

CMD ["/usr/local/bin/start-skydnir-test-suite"]
