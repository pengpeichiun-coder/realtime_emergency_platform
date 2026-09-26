FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.31-alpine
RUN apk upgrade --no-cache libexpat
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
ENTRYPOINT ["nginx", "-g", "daemon off;"]
