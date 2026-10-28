"""Update public page metadata without depending on the homepage's wording."""

import html
import re
import unicodedata


# These slugs are existing public URLs; keep them stable for incoming links.
BLOG_METADATA = {
    "ntrip-caster-la-gi": {
        "title": "NTRIP Caster là gì? Toàn tập về pipeline RTK | Hệ thống Base Aitogy",
        "desc": "Giải thích kiến trúc NTRIP Caster, luồng dữ liệu RTCM, mô hình phân phối mountpoint và các lỗi hay gặp khi triển khai.",
    },
    "huong-dan-ket-noi-cors": {
        "title": "Hướng dẫn kết nối Base cho Drone và máy thi công | Hệ thống Base Aitogy",
        "desc": "Quy trình cấu hình tài khoản NTRIP, kiểm tra FIX, tối ưu độ trễ và xử lý các lỗi mất fix khi làm việc ngoài thực địa.",
    },
    "giam-sat-snr-rtk": {
        "title": "Giám sát SNR và trạng thái RTK để giảm downtime | Hệ thống Base Aitogy",
        "desc": "Mô hình cảnh báo sớm dựa trên SNR, latency và packet loss giúp đội vận hành can thiệp trước khi rover mất FIX hàng loạt.",
    },
    "bao-mat-truyen-dan-cors": {
        "title": "Bảo mật truyền dẫn Base: TLS, phân quyền và audit | Hệ thống Base Aitogy",
        "desc": "Thiết kế lớp bảo mật cho hạ tầng Base theo hướng thực dụng: mã hóa, tách quyền và theo dõi truy cập có truy vết.",
    },
}


def station_slug(value: str) -> str:
    """Match the existing public station URL format used by the browser."""
    normalized = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-zA-Z0-9._-]+", ".", text).strip(".")
    return re.sub(r"\.{2,}", ".", slug).lower()


def page_metadata(source: str, *, title: str, description: str, url: str) -> str:
    source = re.sub(r"<title>.*?</title>", lambda _: f"<title>{html.escape(title)}</title>", source, flags=re.S)
    for tag, attribute, key, value in (
        ("meta", "name", "description", description),
        ("meta", "property", "og:title", title),
        ("meta", "property", "og:description", description),
        ("meta", "property", "og:url", url),
        ("meta", "property", "twitter:title", title),
        ("meta", "property", "twitter:description", description),
        ("meta", "property", "twitter:url", url),
        ("link", "rel", "canonical", url),
    ):
        field = "href" if tag == "link" else "content"
        pattern = rf'(<{tag}\b[^>]*\b{attribute}="{re.escape(key)}"[^>]*\b{field}=")[^"]*(")'
        source = re.sub(pattern, lambda match: match[1] + html.escape(value, quote=True) + match[2], source)
    return source
