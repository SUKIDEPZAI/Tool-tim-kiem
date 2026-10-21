# SearchPilot AI v3 — giao diện + API trên cùng Render Web Service

## Cấu trúc
```text
web-search-bot-v3/
├── app.py
├── requirements.txt
├── Procfile
├── render.yaml
├── .gitignore
└── static/
    └── index.html
```

## Triển khai trên Render
1. Đưa toàn bộ nội dung thư mục này lên thư mục gốc của GitHub repository.
2. Render → New → Web Service → kết nối repository.
3. Runtime: Python.
4. Root Directory: để trống nếu các file ở gốc repo.
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
7. Deploy.

Sau deploy, mở URL Render gốc. `/` hiển thị giao diện; `/api/health`, `/api/keys`, `/api/search` là API.

## API
- `GET /api/health`
- `POST /api/keys` JSON: `{"name":"Tên bot"}` (giới hạn 5 lần/giờ theo IP)
- `GET /api/search?q=tu-khoa&limit=8` với header `X-API-Key: ws_...`

Ví dụ:
```bash
curl -G 'https://YOUR-SERVICE.onrender.com/api/search' \
  --data-urlencode 'q=trí tuệ nhân tạo' \
  --data-urlencode 'limit=8' \
  -H 'X-API-Key: ws_YOUR_KEY'
```

## Lưu ý quan trọng
- API key chỉ hiện một lần lúc tạo; server chỉ lưu SHA-256 hash. Hãy sao chép và cất giữ an toàn.
- SQLite trên ổ đĩa tạm của Render có thể mất dữ liệu khi redeploy/restart hoặc instance thay đổi. Để lưu key bền vững, gắn Persistent Disk và đặt `DATABASE_PATH` vào đường dẫn trên disk, hoặc chuyển sang PostgreSQL.
- Giới hạn theo IP dùng bộ nhớ mặc định; với nhiều worker/instance hãy cấu hình Redis bằng `RATELIMIT_STORAGE_URI`.
- Các website tìm kiếm công khai có thể thay đổi cấu trúc hoặc giới hạn request; nguồn nào lỗi sẽ được báo trong `providers`, các nguồn còn lại vẫn có thể trả kết quả.
- Prompt tạo trên giao diện chỉ hướng dẫn AI cách gọi API; bot cần được lập trình/cấu hình để gửi HTTP request và có quyền truy cập API key.
- Không đưa API key bí mật vào frontend công khai, prompt công khai hoặc kho GitHub.
