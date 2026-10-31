# NDF Factory のランナーのイメージ。Node 24・Python・uv・git・gh・claude の CLI を持つ。
# NDF は焼き込まず、ジョブの中で NDF_VERSION の版をマーケットプレイスから入れる（受け入れ条件 24）。
# 秘密（App の秘密鍵・アクセスキー）をイメージにも環境変数にも置かない（I43）
FROM node:24-bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends git gh python3 python3-venv ca-certificates curl \
  && rm -rf /var/lib/apt/lists/* \
  && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
  && npm install -g @anthropic-ai/claude-code
WORKDIR /app
COPY package.json package-lock.json ./
COPY contracts ./contracts
COPY runner ./runner
RUN npm ci --omit=dev -w runner -w contracts
USER node
ENV RUNNER_WORK_DIR=/home/node/work
CMD ["node", "runner/src/main.ts"]
