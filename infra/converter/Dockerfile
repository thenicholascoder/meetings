FROM gotenberg/gotenberg:8

USER root

# Same font set as the backend image. Gotenberg already ships Carlito, Caladea,
# and Liberation; this adds the Microsoft core fonts and Google Slides families.
COPY infra/fonts/install-slide-fonts.sh /tmp/install-slide-fonts.sh
RUN sed -i 's/\r$//' /tmp/install-slide-fonts.sh \
    && chmod +x /tmp/install-slide-fonts.sh \
    && /tmp/install-slide-fonts.sh \
    && rm /tmp/install-slide-fonts.sh

USER gotenberg
