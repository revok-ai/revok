# Next.js dev container — hot-reload via bind mount
FROM node:20-alpine

WORKDIR /app

# Install deps first for layer cache; package-lock is bind-mounted at runtime
# but we still need a baseline install in the image so the container can
# start before the host volume is mounted (then the mounted node_modules
# wins via the named volume in docker-compose).
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
