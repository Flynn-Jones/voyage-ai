FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY css/styles.css /usr/share/nginx/html/css/styles.css
COPY js/app.js /usr/share/nginx/html/js/app.js
COPY js/multi-agent.js /usr/share/nginx/html/js/multi-agent.js

EXPOSE 80
