FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
ENV LLAMA_TIMEOUT_SECONDS=300
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/15-api-auth.envsh /docker-entrypoint.d/15-api-auth.envsh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
