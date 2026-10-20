# Political Ads Web

React + TypeScript + Vite cho platform Tuần 15. Browser chỉ gọi FastAPI qua Vite proxy, không gọi BigQuery trực tiếp

## Chạy local

Terminal A, từ project root:

```bash
.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Terminal B:

```bash
cd /Users/hoangyugi001/Documents/Coder/political-ads-intelligence-platform/apps/web
npm ci
npm run dev
```

Mở `http://127.0.0.1:5173`. Giữ cả hai terminal chạy khi xem web

## Kiểm tra

```bash
npm run build
npm run lint
npm run test:e2e
npm run test:live
```

`test:e2e` dùng HTTP fixture và không cần API. `test:live` cần API, Gold và forecast local; nó đối soát SQL–API–web, lưu sáu screenshot cùng evidence tại `../../reports/week15/`

Playwright dùng một Chromium worker để phù hợp Mac 8 GB RAM. `node_modules/`, `dist/`, `playwright-report/` và `test-results/` không được commit
