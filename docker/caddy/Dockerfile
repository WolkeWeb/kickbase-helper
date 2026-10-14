FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/ionos

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile.ionos /etc/caddy/Caddyfile
COPY entrypoint.sh /usr/local/bin/caddy-entrypoint
RUN chmod +x /usr/local/bin/caddy-entrypoint
ENTRYPOINT ["/usr/local/bin/caddy-entrypoint"]
