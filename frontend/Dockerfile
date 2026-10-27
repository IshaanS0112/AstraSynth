FROM node:22-alpine AS build
WORKDIR /app
# npm ci uses the committed lockfile, so the image builds the exact
# dependency tree that was tested rather than re-resolving it.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
