"""Seed/upsert các preset mẫu vào DB local. Chạy: python seed_presets.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db

CAPTION_PROMPT = """Viết lại caption dưới đây theo phong cách Royal Viral News (Facebook hoàng gia quốc tế).

<CAPTION CŨ>

Giữ nguyên nhân vật, sự kiện, mối quan hệ, bối cảnh, ý chính và các thông tin quan trọng trong caption gốc. Không tạo thêm câu chuyện mới, nhân vật mới hoặc sự kiện mới. Tuy nhiên, được phép sáng tạo lại cách diễn đạt, thay đổi hook, nhịp kể và cách dẫn dắt để caption hấp dẫn, kịch tính và viral hơn.
Giữ nguyên ngôn ngữ gốc
Giữ độ dài, format và nhịp đọc tương tự caption gốc.

Áp dụng công thức Royal Viral News:
- Mở đầu bằng HOOK MẠNH VIẾT HOA tạo cảm giác breaking news (BREAKING NEWS, ROYAL UPDATE, PALACE ANNOUNCEMENT, SHOCKING NEWS hoặc cụm tương tự phù hợp với quốc gia).
- Nếu caption gốc có yếu tố thời gian/con số tạo cảm giác nóng (15 phút trước, 30 phút trước...) có thể điều chỉnh để tăng tính viral; nhưng các con số quan trọng của câu chuyện (23 năm, tuổi, ngày tháng, sự kiện...) phải giữ nguyên.
- Làm nổi bật nhân vật hoàng gia trung tâm và khoảnh khắc đáng chú ý nhất.
- Tăng cảm xúc dựa trên nội dung gốc: bất ngờ, xúc động, bí mật, quyết định quan trọng, phản ứng công chúng.
- Tạo curiosity gap bằng câu bỏ lửng (...), chi tiết chưa tiết lộ hoặc câu hỏi mở để khiến người đọc muốn xem tiếp.
- Kết thúc bằng CTA ngắn như: Read more in comments / See details below / Discover the full story 👇/ ...👇👇 .

Cấu trúc:
HOOK → Nhân vật + sự kiện → Chi tiết gây tò mò/cảm xúc → Phản ứng → CTA.

Văn phong: giống trailer tin nóng hoàng gia, ngắn gọn, kịch tính, cảm xúc, dễ viral trên Facebook."""

KEYWORD_PROMPT = """Từ caption bài viết này tìm kiếm thật nhiều hình ảnh liên quan của bài viết này, các hình ảnh mang tính kịch tính, hấp dẫn, gây tò mò, có drama, scandal, khóc, mâu thuẫn,...
<CAPTION MỚI TẠO>

Trả lời đúng một JSON list gồm tối đa 6 cụm từ tìm kiếm bằng tiếng Anh (mỗi cụm 2–5 từ), không kèm giải thích."""

SEO_PROMPT = """DỰA VÀO CAPTION Ở TRÊN BẠN ĐÃ TẠO:
<CAPTION MỚI TẠO>
Viết cho tôi một bài viết tiếng Anh dài 1300–1500 từ chuẩn SEO dựa trên nội dung caption này. Ngôn ngữ bài viết phải phụ thuộc vào ngôn ngữ của caption gốc:
- Nếu caption được viết bằng ngôn ngữ nào, hãy viết bài báo bằng chính ngôn ngữ đó.
- Giữ cách dùng từ, phong cách và văn hóa phù hợp với quốc gia hoàng gia trong caption.
- Không tự dịch sang tiếng Anh nếu caption gốc không phải tiếng Anh.

Đây là bản full storytelling mở rộng từ caption gốc. Hãy phát triển câu chuyện bằng cách bổ sung bối cảnh, cảm xúc, suy nghĩ nhân vật, phản ứng công chúng và những chi tiết hợp lý để tạo thành một bài viết hoàn chỉnh, nhưng phải giữ nguyên nhân vật, mối quan hệ, sự kiện chính và ý tưởng cốt lõi của caption gốc.

YÊU CẦU:
- Viết kịch tính, hấp dẫn nhất có thể, có thể fake news để tăng thêm kịch tính, miễn sao đưa bài báo đến đoạn cao trào nhất
- Viết theo phong cách Royal Storytelling / Royal News Feature.
- Giọng văn giống một bài viết về hoàng gia trên tạp chí quốc tế: trang trọng, cảm xúc, cuốn hút.
- Có mở bài gây tò mò, phần nội dung chi tiết và kết luận hợp lý.
- Chia bài thành 7–8 đề mục rõ ràng.
- Mỗi đề mục gồm 3–5 đoạn văn ngắn.
- Mỗi đoạn phải có các nội dung gây shock, kịch tính, gây tò mò, mâu thuẫn, drama
- Giữa các đoạn để double space để dễ đọc.
- Không viết thành một khối chữ dài.
- Không lặp ý, không kéo dài nội dung một cách máy móc.
- Không nhồi từ khóa SEO.

Cách triển khai:
- Tiêu đề bài viết phải là caption ở trên
- Mở đầu bằng khoảnh khắc gây chú ý nhất, độ hấp dẫn mang tính tò mò nhất trong bài báo.
- Phát triển bối cảnh hoàng gia, vai trò của nhân vật và ý nghĩa của sự kiện.
- Khai thác cảm xúc, trách nhiệm, gia đình, truyền thống, áp lực và phản ứng của công chúng.
- Tạo nhịp kể như một câu chuyện đang được hé lộ từng phần.
- Xuyên suốt bài báo phải đảm bảo có nội dung gây shock, kịch tính, gây tò mò, mâu thuẫn, drama
- Kết thúc bằng một đoạn kết cảm xúc, nhấn mạnh ý nghĩa của khoảnh khắc đối với hoàng gia và người hâm mộ.

Lưu ý:
- Có thể thêm chi tiết mô tả, cảm xúc, bối cảnh và diễn biến hợp lý để bài viết tự nhiên hơn.
- Nếu caption có yếu tố chưa được xác nhận, hãy viết theo hướng storytelling/giả định, không khẳng định như một sự kiện đã được chứng minh.

Nội dung phải phù hợp với chính sách quảng cáo:
- Không sử dụng nội dung 18+, tình dục trực diện, ngôn từ thù ghét hoặc phân biệt đối xử.
- Không mô tả chi tiết bạo lực, máu me, thương tích nghiêm trọng hoặc tổn thương trẻ em.
- Nếu có xung đột, tập trung vào cảm xúc, tâm lý nhân vật, trách nhiệm và tác động xã hội.

Ưu tiên:
- Giữ chân người đọc.
- Tạo cảm giác đang khám phá một câu chuyện hoàng gia đặc biệt.
- Viết tự nhiên như một bài báo storytelling cao cấp."""

PRESETS = [
    {
        "name": "Royal Viral News",
        "description": "Re-caption viral phong cách hoàng gia + SEO storytelling. Dán caption cũ vào ô 'Chi tiết / dữ kiện' rồi bấm ⚡ Chạy cả pipeline.",
        "caption": CAPTION_PROMPT,
        "keyword": KEYWORD_PROMPT,
        "seo": SEO_PROMPT,
        "input": "",
    },
]


def main():
    conn = db.connect()
    existing = {p["name"]: p for p in db.list_presets(conn)}
    for item in PRESETS:
        p = existing.get(item["name"])
        db.save_preset(
            conn,
            p["id"] if p else None,
            item["name"],
            item["description"],
            item["caption"],
            item["keyword"],
            item["seo"],
            item["input"],
            p["provider_id"] if p else None,
        )
        print(("updated " if p else "created ") + item["name"])


if __name__ == "__main__":
    main()
