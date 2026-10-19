# Web Search Bot

Ứng dụng gồm giao diện HTML tĩnh và backend API chạy trên Render Web Service.

## Cấu trúc

- `index.html`: giao diện, tạo key, tìm kiếm và sao chép prompt.
- `backend/app.py`: API Flask, lưu hash API key bằng SQLite, tìm kiếm DuckDuckGo HTML.
- `backend/requirements.txt`: thư viện Python.

## Triển khai backend lên Render

1. Đưa thư mục `backend` lên một GitHub repository riêng hoặc đưa nội dung của nó vào thư mục gốc repository backend.
2. Tạo **New → Web Service** trên Render, kết nối repository.
3. Nếu backend nằm trong thư mục `backend`, đặt **Root Directory** là `backend`.
4. Runtime: Python.
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `gunicorn app:app`
7. Thêm Environment Variable:
   - `FRONTEND_ORIGIN` = URL GitHub Pages, ví dụ `https://TEN-USER.github.io/TEN-REPO`
8. Deploy, mở `https://TEN-DICH-VU.onrender.com/api/health`, kỳ vọng nhận `{"ok":true,...}`.

## Triển khai giao diện GitHub Pages

1. Đưa `index.html` lên repository frontend.
2. Vào **Settings → Pages**.
3. Chọn deploy từ branch `main`, folder `/ (root)`, lưu lại.
4. Mở website Pages, nhập URL backend Render vào ô URL.

## Lưu ý quan trọng

- SQLite trên Render có thể mất dữ liệu khi deploy/restart nếu không gắn Persistent Disk. Với bản dùng lâu dài, hãy gắn Render Persistent Disk và đặt `DATABASE_PATH` vào đường dẫn mount, hoặc chuyển sang PostgreSQL.
- Endpoint tạo key hiện chưa có đăng nhập, CAPTCHA hay giới hạn tốc độ; người biết URL có thể tự tạo nhiều key. Thêm bảo vệ trước khi public rộng rãi.
- DuckDuckGo HTML scraping không phải API chính thức, có thể bị giới hạn hoặc thay đổi giao diện. Để ổn định hơn, thay hàm `search_duckduckgo` bằng nhà cung cấp có API chính thức; một số nhà cung cấp yêu cầu key hoặc có hạn mức miễn phí.
- Không đưa secret vào GitHub. API key do ứng dụng tạo chỉ là khóa truy cập backend này, không phải key của OpenAI/Google/Brave/Tavily.
