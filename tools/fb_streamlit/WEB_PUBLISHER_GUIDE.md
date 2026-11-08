# Grid → Website Publisher (bản vá thử nghiệm)

## Ánh xạ đã chốt

- `caption.txt` (B1) → website **Title** (giữ nguyên caption).
- Ảnh B2 (`stories.image_of`) → **Featured Image** (tải file).
- `seo.md` (B3) → **Description** (chuyển Markdown thành HTML).
- **Không** điền SEO Title, SEO Keywords, SEO Description.
- Slug/Categories/Tags không tự đặt; giữ giá trị mặc định của website.
- Bật Published, bấm Save, tìm **đúng hàng có Title**, lấy URL thực tế, lưu `story.json.link`.

## Cài đặt trên máy chạy Streamlit

1. Sao lưu công cụ cũ. Chép `web_publisher.py`, `grid.py`, `requirements.txt` từ bản vá vào `tools/fb_streamlit/`.
2. Trong đúng virtualenv của Streamlit chạy `python -m pip install -r requirements.txt`.
3. Chạy `python -m playwright install chromium`.
4. Khởi động lại Streamlit, vào **Grid → 🌐 Đăng báo lên website → lấy Link**.
5. Bấm **Mở Chromium và đăng nhập thủ công**; đăng nhập trên cửa sổ vừa mở rồi đóng cửa sổ đó. Phiên đăng nhập nằm cục bộ dưới workspace `.web-publisher-browser/` — **không chia sẻ folder này**.
6. Chọn **1 Story ID** đã có B1, B2, B3 và chưa có Link. Kiểm tra kết quả trên website trước khi chạy nhiều bài.

**Đã cập nhật theo HTML người dùng cung cấp ngày 08/11/2026.** Selector mặc định khớp mẫu HTML (không còn là selector phỏng đoán). Tuy nhiên chưa thể kiểm thử đăng thật hoặc xác nhận hành vi JS và đăng nhập từ HTML tĩnh.

## Selector đã xác nhận từ HTML

- New post: `button:has-text("New post")`
- Title: `label.field-wrapper:has(> span:text-is("Title")) input` (`maxlength=1024`)
- B2 upload: `.post-image-upload-surface input[type="file"][accept="image/*"]`
- B3 Description: TinyMCE, iframe `.tox-edit-area__iframe` → `body#tinymce`. Nhập bằng API TinyMCE ở chính trang quản trị; nếu editor không nhận hãy kiểm tra lúc chạy thật.
- Published: `label:has-text("Published") button[role="checkbox"]`, dựa trên `aria-checked`.
- Save: `form.post-editor-form button[type="submit"]:has-text("Save")`.
- Danh sách Posts: `table.posts-responsive-table tbody tr`.
- **Link công khai**: `.posts-row-actions a[title="View post"][href]`, lấy đúng giá trị href (không đoán slug và không cần clipboard).
- Mã HTML mẫu có bài công khai trên `newsphvan.danhngon.pro` dù trang quản trị ở `usnewspv247.danhngon.pro`. Bộ xác thực URL chấp nhận HTTPS path `/blog/` trên subdomain `.danhngon.pro`; cần đảm bảo bạn đang đăng lên đúng site được chọn trong tài khoản.
- Slug, Categories, Tags, 3 trường SEO **không tự động điền**.

## Cách đăng nhập

- Trong Grid bấm `🔐 Mở Chromium và đăng nhập thủ công`. Trình duyệt Chromium chạy trên **máy đang chạy Streamlit**, không tự mở trên máy client nếu Streamlit ở VPS/server.
- Đăng nhập tài khoản website trực tiếp trong Chromium (tự nhập mật khẩu và xác thực 2 bước nếu có). Đợi giao diện Posts hiện nút **New post**, sau đó đóng cửa sổ trình duyệt.
- Playwright lưu trạng thái đăng nhập vào thư mục `.web-publisher-browser/` ở workspace. Đây là **dữ liệu nhạy cảm**, không chép hoặc chia sẻ, nên đặt workspace trên máy riêng và hạn chế quyền đọc file.
- Nếu hết hạn phiên: nhấn nút mở Chromium, đăng nhập lại bằng chính profile này. Không gửi mật khẩu, session token, cookie vào hội thoại.
- Chrome/Edge đang đăng nhập sẵn thông thường **không chia sẻ phiên** với Playwright Chromium; cần đăng nhập một lần trong cửa sổ Playwright.

## Kiểm thử

1. Chạy duy nhất **1 story** có B1/B2/B3, không có Link. Kiểm tra website khi đăng và nội dung sau Save.
2. Vào admin/posts để xác minh bài nằm đúng site, tiêu đề trọn vẹn và ảnh B2 xuất hiện.
3. Kiểm tra cột Link Grid khớp `View post` của hàng vừa đăng.
4. Nếu Save đã thực hiện nhưng không tìm được link, trạng thái `needs_review` khóa retry. Đối soát URL từ Posts rồi mới tiếp tục.

## Chống đăng trùng, an toàn thao tác

- Nếu `story.json.link` đã có giá trị, bài bị bỏ qua.
- Ngay **trước khi bấm Save**, hệ thống đặt `web_publish.state = saving`.
- Nếu không lấy được URL sau Save, chuyển thành `needs_review`; **không tự retry đăng** vì có thể website đã tạo bài.
- Dùng `/admin/posts` xác minh bài, điền đúng URL vào phần **Đối soát** trên Grid; URL được lưu vào cùng trường `link` cũ.
- Nếu sai selector trước khi bấm Save, có thể sửa cấu hình và thử lại vì chưa đụng website; không khởi tạo trạng thái `saving`.
- Bản này không tự động chạy ngầm, không xử lý captcha/2FA, không vượt kiểm soát đăng nhập, không đăng đồng thời nhiều bài.

## Hạn chế hiện tại

- Đã có HTML tĩnh xác nhận các selector chính nhưng chưa xác minh JS sau Save, thời gian upload ảnh và hành vi đăng thật.
- Một số editor đòi thao tác nhập qua API của chính editor để cập nhật React/Vue state. Nếu sau Save nội dung bị trống, cần sửa `_fill_editor` dựa trên HTML/JS editor.
- Tìm hàng bằng Title có thể không hoạt động nếu bảng rút gọn Title, phân trang, hoặc bài có cùng Title: khi đó hệ thống **dừng để đối soát**, không lấy nhầm link.
- Chạy ứng dụng trên server không có desktop thì nút đăng nhập Chromium dạng có giao diện sẽ không hoạt động; cần desktop/remote desktop.
- Không nhập token, mật khẩu hoặc cookie vào cấu hình.
