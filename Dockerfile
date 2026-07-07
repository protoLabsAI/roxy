# roxy — Portfolio Manager (stock protoAgent + the pm-stack bundle).
#
# INLINE WITH PROTOAGENT: roxy is NOT a code fork (she was — 730/1568 diverged;
# that fork is preserved on protoLabsAI/roxy@protocontent-roxy). She runs the stock
# upstream image with the Portfolio Manager bundle (github.com/protoLabsAI/
# portfolio-manager-stack) baked in: delegates + portfolio + github enabled, and
# project_board + agent_browser installed-off so the Lead Engineer teams she spins up
# discover them on this host. Rebuild to track upstream — nothing pins a core commit.
#
# Mirrors the jon thin-image pattern: clone each bundle plugin to the bundle plugins
# dir at the pm-stack-pinned ref (discovered by id when enabled in config), seed
# config + SOUL via PROTOAGENT_SEED_CONFIG/SEED_SOUL (seed-not-force: operator console
# edits persist in the config volume), and add the coder runtime (proto + git) the
# teams she spawns need.
FROM ghcr.io/protolabsai/protoagent:latest

USER root

# --- pm-stack bundle plugins (pinned to the bundle's verified refs) --------------
# All PUBLIC — plain shallow clones, no token. .git dropped after (slims the layer,
# strips no credential since these are anonymous). Bump these with the pm-stack pins.
RUN set -eux; \
    clone() { git clone --depth 1 --branch "$2" "$1" "/opt/protoagent/plugins/$3" && rm -rf "/opt/protoagent/plugins/$3/.git"; }; \
    clone https://github.com/protoLabsAI/portfolio-plugin       v0.16.0 portfolio; \
    clone https://github.com/protoLabsAI/github-plugin          v0.1.3  github; \
    clone https://github.com/protoLabsAI/projectBoard-plugin    v0.32.0 project_board; \
    clone https://github.com/protoLabsAI/agent-browser-plugin   v0.5.1  agent_browser

# --- coder runtime for spun-up Lead Engineer teams ------------------------------
# A spawned team's project_board spawn loop dispatches builds to the `proto` acp coder
# in per-feature git worktrees (git is already in the base). Node 20 LTS via NodeSource;
# pin PROTOCLI_VERSION to upgrade.
ARG PROTOCLI_VERSION=latest
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@protolabsai/proto@${PROTOCLI_VERSION}" \
    && command -v proto && proto --version || (echo "proto install failed" >&2; exit 1)

# --- config + persona seeds (seed-not-force; never clobbers live operator edits) --
COPY config/langgraph-config.yaml /opt/roxy/seed/langgraph-config.yaml
ENV PROTOAGENT_SEED_CONFIG=/opt/roxy/seed/langgraph-config.yaml
COPY config/SOUL.md /opt/protoagent/config/SOUL.md
COPY config/SOUL.md /opt/roxy/seed/SOUL.md
ENV PROTOAGENT_SEED_SOUL=/opt/roxy/seed/SOUL.md

# --- team template roxy clones per spawned team (carries the managed-git coder) ---
COPY config/team-template /opt/roxy/team-template

# roxy's entrypoint preps the coder runtime (proto -> gateway, protoContent clone)
# then execs the stock upstream launch.
COPY entrypoint.sh /opt/protoagent/entrypoint.roxy.sh
RUN chmod +x /opt/protoagent/entrypoint.roxy.sh \
    && chown -R sandbox:sandbox /opt/protoagent/config /opt/roxy

USER 1001
WORKDIR /sandbox
CMD ["/opt/protoagent/entrypoint.roxy.sh"]
