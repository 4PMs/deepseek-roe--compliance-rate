FROM bkimminich/juice-shop:v20.2.0

COPY --chown=65532:65532 environments/juice_shop/sequelize_observer.cjs /juice-shop/tempera-sequelize-observer.cjs
ENV NODE_OPTIONS="--require=/juice-shop/tempera-sequelize-observer.cjs"
ENV TEMPERA_DB_OBSERVER="host.docker.internal:8765"
