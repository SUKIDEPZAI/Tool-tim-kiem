# Web Search Bot v2 — GitHub Pages + Render

## Đã rà soát và nâng cấp

- Tìm song song từ DuckDuckGo HTML, Wikipedia API và Google News RSS.
- Gộp kết quả, loại URL trùng, xếp hạng đơn giản theo mức khớp truy vấn.
- Mỗi nguồn có trạng thái thành công/lỗi; một nguồn hỏng không nhất thiết làm hỏng toàn bộ truy vấn.
- Timeout kết nối/đọc, giới hạn độ dài query, giới hạn `limit`, mã lỗi JSON nhất quán.
- API key ngẫu nhiên, chỉ lưu SHA-256 hash; key thô chỉ trả về một lần.
- Rate limit: tạo key 5 lần/giờ/IP; tìm kiếm 30 lần/phút/IP (bộ nhớ tiến trình mặc định).
- CORS có thể giới hạn theo `FRONTEND_ORIGIN`.
- Có kiểm thử cơ bản cho health, xác thực, validation và gộp kết quả.

## Cấu trúc

- `index.html`: giao diện GitHub Pages.
- `backend/app.py`: Flask API.
- `backend/requirements.txt`: dependencies.
- `backend/tests/test_app.py`: tests.

## Deploy backend

1. Đưa nội dung thư mục `backend` vào root của repository backend.
2. Tạo Render **Web Service** và kết nối repository.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
5. Đặt `FRONTEND_ORIGIN=https://TEN-USER.github.io/TEN-REPO`. Nếu có nhiều origin, phân cách bằng dấu phẩy.
6. Để giữ database qua deploy/restart, gắn Persistent Disk tại `/var/data`, đặt `DATABASE_PATH=/var/data/keys.db`.
7. Deploy, sau đó kiểm tra `/api/health`.

## Deploy frontend

Đưa `index.html` vào root của repository frontend → GitHub Settings → Pages → deploy từ branch `main`, folder `/(root)`. Mở website, nhập URL backend Render, kiểm tra kết nối.

## Chạy test cục bộ

Trong thư mục `backend`:

```bash
pip install -r requirements.txt
pytest -q
```

## Hạn chế / bảo mật cần hiểu

- DuckDuckGo HTML scraping không phải API chính thức và có thể bị chặn/thay đổi.
- Wikipedia và Google News có thiên hướng nội dung riêng; kết quả không thay thế nguồn chuyên biệt.
- Rate limit `memory://` bị reset khi tiến trình khởi động lại và không chia sẻ giữa nhiều worker/instance. Khi public, cấu hình Redis bằng `RATELIMIT_STORAGE_URI`.
- Endpoint tạo key vẫn công khai; giới hạn theo IP chỉ là lớp chống lạm dụng cơ bản. Bản public lớn nên thêm CAPTCHA/đăng nhập, quản lý thu hồi key, hạn mức theo key và giám sát.
- Chỉ dùng CORS không phải cơ chế bảo mật. Luôn giữ API key bí mật và không commit key lên GitHub.
- Chưa thể khẳng định kết quả live của từng nhà cung cấp nếu chưa deploy và kiểm tra trên môi trường Render thật.
