# EstateFlow CRM - Production Deployment Guide
**Domain**: `https://crm.thesspartners.com`  
**Server IP**: `200.141.10.71` (VPS)

---

## Architecture on VPS
- **Web Admin Frontend**: Built static files (`/var/www/estateflow/web/dist`) served by Nginx.
- **Backend API**: FastAPI service managed by `systemd` running via Uvicorn on `127.0.0.1:8000`.
- **Reverse Proxy**: Nginx handles SSL (Certbot Let's Encrypt), routes `/api/` to port 8000, and routes all other traffic to `index.html`.

---

## 1. Systemd Service (`/etc/systemd/system/estateflow-backend.service`)
```ini
[Unit]
Description=EstateFlow CRM FastAPI Backend
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/estateflow/backend
Environment="PATH=/var/www/estateflow/backend/venv/bin"
EnvironmentFile=/var/www/estateflow/backend/.env
ExecStart=/var/www/estateflow/backend/venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 4

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Commands to enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable estateflow-backend
sudo systemctl start estateflow-backend
sudo systemctl status estateflow-backend
```

---

## 2. Nginx Configuration (`/etc/nginx/sites-available/crm.thesspartners.com`)
```nginx
server {
    server_name crm.thesspartners.com;

    # Admin Web Static Build
    root /var/www/estateflow/web/dist;
    index index.html;

    client_max_body_size 25M;

    # Frontend Single Page App Routing
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend API Reverse Proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90;
    }

    # API Documentation
    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        proxy_pass http://127.0.0.1:8000/openapi.json;
        proxy_set_header Host $host;
    }
}
```

Enable site & obtain SSL:
```bash
sudo ln -s /etc/nginx/sites-available/crm.thesspartners.com /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d crm.thesspartners.com
```
