FROM nginx:alpine

COPY templates/ /usr/share/nginx/html/
COPY css/ /usr/share/nginx/html/css/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80