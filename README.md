# SearchPilot AI v5 — Deep GitHub & Code Search

## Tính năng
- Giao diện + API trong cùng Render Web Service.
- GitHub repository search chạy nhiều truy vấn bổ sung: tìm theo tên/mô tả/README index và truy vấn `stars:0..5` để không bỏ qua dự án nhỏ.
- Sourcegraph public indexed-code search để tìm các file/đoạn mã khi dịch vụ công khai khả dụng.
- Kết quả repository gồm URL, description, ngôn ngữ, stars, forks, kích thước, topics, license, created/updated, archived.
- Cache ngắn hạn để giảm request lặp lại; trạng thái provider hiển thị rõ.
- Không yêu cầu API key tìm kiếm bên thứ ba. API key do chính SearchPilot tạo để bảo vệ endpoint của website.

## Cấu trúc
```text
searchpilot-ai-v5/
├── app.py
├── requirements.txt
├── Procfile
├── render.yaml
├── .gitignore
└── static/index.html
```

## Deploy Render
Upload toàn bộ nội dung thư mục lên gốc repo GitHub.
- Root Directory: để trống
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
- Health check: `/api/health`

## API
- `GET /api/health`
- `POST /api/keys` JSON `{"name":"My bot"}`
- `GET /api/search?q=esp32+nat+router&limit=30&mode=github`
- Header `X-API-Key: ws_...`

Modes: `github`, `code`, `all`, `web`, `news`, `technical`, `academic`, `knowledge`.

## Giới hạn quan trọng
- GitHub anonymous API có giới hạn. Search endpoints có hạn mức nghiêm ngặt hơn; nếu 403/429, chờ reset và không retry liên tục.
- Search repository tìm dựa trên metadata/tên/mô tả/README được GitHub index, không trả toàn bộ repository code.
- Sourcegraph chỉ tìm code trong phạm vi index/khả năng truy cập của dịch vụ; nó không bao phủ mọi repository hay mọi nhánh.
- “Tất cả dự án, dù nhỏ đến đâu” không thể bảo đảm tuyệt đối. Bản này cố ý thêm truy vấn `stars:0..5`, nhưng vẫn bị giới hạn bởi index, truy vấn, quota và kết quả trả về.
- Trước khi tích hợp dự án vào ứng dụng, hãy kiểm tra license, dependencies, bảo mật, bảo trì và khả năng tương thích. Không tự động nhập toàn bộ mã nguồn của dự án vào sản phẩm.
- SQLite trên ổ đĩa tạm có thể mất dữ liệu sau restart/redeploy. Dùng Persistent Disk với `DATABASE_PATH` hoặc PostgreSQL để lưu key bền vững.
- Rate limiting mặc định dùng memory; với nhiều instance cấu hình Redis qua `RATELIMIT_STORAGE_URI`.
