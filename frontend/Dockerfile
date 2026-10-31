FROM node:24-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# --host so the dev server listens on 0.0.0.0, not just the container's own
# localhost -- otherwise the published 5173 port reaches nothing.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
