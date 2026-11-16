# Local-only image that renders .pptx decks to PNG for the README previews (LibreOffice + poppler).
# Nothing is installed on the host. Build: docker build -t p3-deck-preview docker/deck-preview
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc

RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-impress poppler-utils \
      fonts-liberation fonts-noto-core fontconfig \
 && rm -rf /var/lib/apt/lists/*

# Arial is the deck font (ships with Windows/macOS); map it to Liberation Sans, with Noto Sans Arabic for Arabic.
COPY fonts.conf /etc/fonts/local.conf
RUN fc-cache -f

RUN useradd -m render
USER render
WORKDIR /work
COPY --chown=render render.sh /usr/local/bin/render-deck
ENTRYPOINT ["/usr/local/bin/render-deck"]
